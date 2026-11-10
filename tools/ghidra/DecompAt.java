// Create functions at given addresses if missing, then decompile them to a file. Static only.
//@category AULA

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompAt extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(new File(args[0]), "UTF-8")) {
            for (int i = 1; i < args.length; i++) {
                Address a = toAddr(Long.decode(args[i]));
                Function f = getFunctionAt(a);
                if (f == null) {
                    disassemble(a);
                    f = createFunction(a, null);
                }
                out.printf("%n// ===== %s @ %s =====%n", f == null ? "?" : f.getName(), a);
                if (f == null) {
                    out.println("// could not create function");
                    continue;
                }
                DecompileResults r = ifc.decompileFunction(f, 120, monitor);
                out.println(r != null && r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// failed");
            }
        }
        ifc.dispose();
        println("DecompAt done");
    }
}
