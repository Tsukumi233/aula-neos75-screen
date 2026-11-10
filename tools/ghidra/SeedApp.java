// Pre-analysis for an app image at 0x8000 without hard-coded names (addresses differ between builds).
// Adds RAM/peripheral/XIP blocks and seeds functions from the vector table, BL targets and the RTOS task table.
// Static only.
//@category AULA

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;

public class SeedApp extends GhidraScript {

    private void block(String name, long start, long size, boolean io) throws Exception {
        Memory mem = currentProgram.getMemory();
        if (mem.getBlock(toAddr(start)) != null) {
            return;
        }
        MemoryBlock b = mem.createUninitializedBlock(name, toAddr(start), size, false);
        b.setRead(true);
        b.setWrite(true);
        b.setExecute(false);
        b.setVolatile(io);
    }

    private boolean inImage(long va) {
        MemoryBlock b = currentProgram.getMemory().getBlock(toAddr(va));
        return b != null && b.isInitialized();
    }

    private int seed(long target) throws Exception {
        if (!inImage(target) || getFunctionAt(toAddr(target)) != null) {
            return 0;
        }
        Address a = toAddr(target);
        disassemble(a);
        return createFunction(a, null) != null ? 1 : 0;
    }

    @Override
    public void run() throws Exception {
        block("SRAM", 0x20000000L, 0x40000L, false);
        block("EXTRAM", 0x80000000L, 0x200000L, false);
        block("XIP_NOR", 0x70000000L, 0x1000000L, false);
        block("PERIPH", 0x40000000L, 0x100000L, true);
        block("ROM", 0x11000000L, 0x10000L, false);
        block("SCS", 0xE000E000L, 0x1000L, true);

        Memory mem = currentProgram.getMemory();
        long base = 0x8000L;
        MemoryBlock img = mem.getBlock(toAddr(base));
        long end = img.getEnd().getOffset();
        int seeded = 0;

        for (int i = 1; i < 128; i++) {
            long v = mem.getInt(toAddr(base + 4L * i)) & 0xFFFFFFFFL;
            if ((v & 1) == 1 && inImage(v & ~1L)) {
                seeded += seed(v & ~1L);
            }
        }

        byte[] buf = new byte[(int) (end - base + 1)];
        mem.getBytes(toAddr(base), buf);
        for (int off = 0; off + 4 <= buf.length; off += 2) {
            int h1 = (buf[off] & 0xFF) | ((buf[off + 1] & 0xFF) << 8);
            int h2 = (buf[off + 2] & 0xFF) | ((buf[off + 3] & 0xFF) << 8);
            if ((h1 & 0xF800) != 0xF000 || (h2 & 0xD000) != 0xD000) {
                continue;
            }
            int s = (h1 >> 10) & 1;
            int i1 = 1 - (((h2 >> 13) & 1) ^ s);
            int i2 = 1 - (((h2 >> 11) & 1) ^ s);
            long imm = ((long) s << 24) | ((long) i1 << 23) | ((long) i2 << 22) | ((long) (h1 & 0x3FF) << 12) | ((long) (h2 & 0x7FF) << 1);
            if (s == 1) {
                imm -= 1L << 25;
            }
            long t = base + off + 4 + imm;
            if (t >= base && t < end && (t & 1) == 0) {
                seeded += seed(t);
            }
        }

        // Thumb pointers to code inside 4-byte-aligned data words (task tables, callback tables).
        for (int off = 0; off + 4 <= buf.length; off += 4) {
            long v = ((buf[off] & 0xFFL) | ((buf[off + 1] & 0xFFL) << 8) | ((buf[off + 2] & 0xFFL) << 16) | ((buf[off + 3] & 0xFFL) << 24));
            if ((v & 1) == 1 && v > base + 0x200 && v < end) {
                long t = v & ~1L;
                int h = (buf[(int) (t - base)] & 0xFF) | ((buf[(int) (t - base) + 1] & 0xFF) << 8);
                if ((h & 0xFE00) == 0xB400 || h == 0xE92D) { // push {...} or push.w
                    seeded += seed(t);
                }
            }
        }
        println("SeedApp: functions seeded " + seeded);
    }
}
