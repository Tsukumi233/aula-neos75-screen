// Pre-analysis: add RAM/peripheral/XIP blocks and seed functions from the vector table and BL targets.
// Static only: reads program bytes, never executes them.
//@category AULA

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;

public class SeedCortexM extends GhidraScript {

    private void block(String name, long start, long size, boolean volatileIo) throws Exception {
        Memory mem = currentProgram.getMemory();
        Address a = toAddr(start);
        if (mem.getBlock(a) != null) {
            return;
        }
        MemoryBlock b = mem.createUninitializedBlock(name, a, size, false);
        b.setRead(true);
        b.setWrite(true);
        b.setExecute(false);
        b.setVolatile(volatileIo);
    }

    private boolean inImage(long va) {
        MemoryBlock b = currentProgram.getMemory().getBlock(toAddr(va));
        return b != null && b.isInitialized();
    }

    private int seed(long target, String name) throws Exception {
        if (!inImage(target)) {
            return 0;
        }
        Address a = toAddr(target);
        if (getFunctionAt(a) != null) {
            return 0;
        }
        disassemble(a);
        if (createFunction(a, name) != null) {
            return 1;
        }
        return 0;
    }

    @Override
    public void run() throws Exception {
        block("SRAM", 0x20000000L, 0x10000L, false);
        block("EXTRAM", 0x80000000L, 0x200000L, false);
        block("XIP_NOR", 0x70000000L, 0x1000000L, false);
        block("PERIPH", 0x40000000L, 0x100000L, true);
        block("ROM", 0x11000000L, 0x10000L, false);
        block("SCS", 0xE000E000L, 0x1000L, true);

        Memory mem = currentProgram.getMemory();
        long base = 0x8000L; // BinaryLoader base; getImageBase() is not set for raw loads
        int seeded = 0;

        // Vector table: entry 0 is SP, then Thumb handler addresses.
        for (int i = 1; i < 128; i++) {
            long v = mem.getInt(toAddr(base + 4L * i)) & 0xFFFFFFFFL;
            if (v == 0) {
                continue;
            }
            if ((v & 1) == 0 || !inImage(v & ~1L)) {
                if (i > 16) {
                    break;
                }
                continue;
            }
            seeded += seed(v & ~1L, i == 1 ? "Reset_Handler" : String.format("vec_%02d", i));
        }

        // Thumb-2 BL targets.
        MemoryBlock img = mem.getBlock(toAddr(base));
        long end = img.getEnd().getOffset() - 16;
        byte[] buf = new byte[(int) (end - base)];
        mem.getBytes(toAddr(base), buf);
        java.util.TreeSet<Long> targets = new java.util.TreeSet<>();
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
                targets.add(t);
            }
        }
        for (long t : targets) {
            seeded += seed(t, null);
        }
        println("SeedCortexM: BL targets " + targets.size() + ", functions seeded " + seeded);

        // Names for addresses already established in this project.
        String[][] names = {
            {"0x34E94", "a5_checksum"}, {"0x35064", "a5_dispatch"}, {"0x37FA8", "upload_block_erase_program"},
            {"0x33550", "widget_percent_update"}, {"0x33BA4", "widget_keylabel_update"}, {"0x33E60", "widget_clock_update"},
            {"0x33DE8", "page_save_delayed"}, {"0x34334", "upload_gui_poll"}, {"0x1CB24", "jpeg_decode_hw"},
            {"0x1C388", "jpeg_parse_headers"}, {"0x9450", "jpeg_hw_submit"}, {"0x24DC4", "lv_img_set_src_like"},
            {"0x12AB4", "gui_flush_480stride"}, {"0x1191C", "page_static_image_enter"}, {"0x1CE60", "cmd13_02_request_dfu"},
            {"0xF904", "set_boot_flag_A5A5"}, {"0x2DF34", "erase_low_page_7000_if_flag"}, {"0x8F64", "iflash_erase_page"},
            {"0x17A38", "config_save"}, {"0x17A68", "config_save_delayed"}, {"0x262C8", "gui_malloc"}, {"0x26454", "gui_free"},
            {"0x1D550", "lcd_st7701_init"}, {"0xA7FC", "lcd_spi9_cmd"}, {"0xA880", "lcd_spi9_data"},
        };
        for (String[] n : names) {
            Address a = toAddr(Long.decode(n[0]));
            seed(a.getOffset(), null);
            if (getFunctionAt(a) != null) {
                getFunctionAt(a).setName(n[1], SourceType.USER_DEFINED);
            }
        }
    }
}
