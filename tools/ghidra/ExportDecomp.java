// Post-analysis: decompile every function into one C file and list functions with callers.
// Static only.
//@category AULA

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportDecomp extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        File outDir = new File(args.length > 0 ? args[0] : ".");
        outDir.mkdirs();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        int ok = 0, fail = 0;
        try (PrintWriter c = new PrintWriter(new File(outDir, "decompiled.c"), "UTF-8");
             PrintWriter idx = new PrintWriter(new File(outDir, "functions.tsv"), "UTF-8")) {
            idx.println("entry\tname\tsize\tcallers\tcallees");
            FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
            while (it.hasNext() && !monitor.isCancelled()) {
                Function f = it.next();
                idx.printf("%s\t%s\t%d\t%d\t%d%n", f.getEntryPoint(), f.getName(), f.getBody().getNumAddresses(),
                        f.getCallingFunctions(monitor).size(), f.getCalledFunctions(monitor).size());
                DecompileResults r = ifc.decompileFunction(f, 60, monitor);
                c.printf("%n// ===== %s @ %s =====%n", f.getName(), f.getEntryPoint());
                if (r != null && r.decompileCompleted()) {
                    c.println(r.getDecompiledFunction().getC());
                    ok++;
                } else {
                    c.println("// decompile failed: " + (r == null ? "null" : r.getErrorMessage()));
                    fail++;
                }
            }
        }
        ifc.dispose();
        println("ExportDecomp: ok " + ok + ", failed " + fail + ", out " + outDir.getAbsolutePath());
    }
}
