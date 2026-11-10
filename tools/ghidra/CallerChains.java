// Print caller chains (up to a depth) for given addresses, plus references to function entries
// from data (e.g. task-create or callback tables). Static only.
//@category AULA

import java.io.File;
import java.io.PrintWriter;
import java.util.HashSet;
import java.util.Set;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class CallerChains extends GhidraScript {
    private PrintWriter out;

    private void refsTo(Function f, String indent) {
        for (Reference r : getReferencesTo(f.getEntryPoint())) {
            if (r.getReferenceType().isCall()) {
                continue;
            }
            Function from = getFunctionContaining(r.getFromAddress());
            out.printf("%s  [ref %s from %s in %s]%n", indent, r.getReferenceType(), r.getFromAddress(),
                    from == null ? "-" : from.getName() + "@" + from.getEntryPoint());
        }
    }

    private void walk(Function f, int depth, int max, Set<Function> seen) throws Exception {
        String indent = "  ".repeat(depth);
        out.printf("%s%s @ %s%n", indent, f.getName(), f.getEntryPoint());
        refsTo(f, indent);
        if (depth >= max || !seen.add(f)) {
            return;
        }
        for (Function c : f.getCallingFunctions(monitor)) {
            walk(c, depth + 1, max, seen);
        }
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        out = new PrintWriter(new File(args[0]), "UTF-8");
        for (int i = 1; i < args.length; i++) {
            Address a = toAddr(Long.decode(args[i]));
            Function f = getFunctionContaining(a);
            out.printf("%n===== chain for %s =====%n", args[i]);
            if (f == null) {
                out.println("no function");
                continue;
            }
            walk(f, 0, 9, new HashSet<>());
        }
        out.close();
        println("CallerChains done");
    }
}
