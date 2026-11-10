"""The keyboard part of the service (in place of Aula Hub): keymap, both knobs written into the keyboard, lighting,
system settings, macros, advanced keys (tap dance), battery. Talks VIA to the keyboard's main controller (`neos75screen.via`); the screen is not
involved. The USB interface is opened for each request and closed again, so Aula Hub (or VIA) can still be used.

On the first successful read a backup of everything this can change is saved (`keyboard_backup.json` next to
the settings); `restore()` writes it back. The backup may hold keycodes that are otherwise never written (the
factory Fn+PrtSc screen reset): restoring them is allowed because they are the keyboard's own values.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .. import keycodes, via
from ..live import library

DEFINITION = "AULA-Neos75_via_v3.json"


def definition_paths() -> list[Path]:
    return [library.ROOT / "design" / "official" / DEFINITION]


class KeyboardControl:
    def __init__(self, data_dir: Path, opener=None, log=None, simulated: bool = False) -> None:  # noqa: ANN001
        self.backup_path = data_dir / "keyboard_backup.json"
        self.names_path = data_dir / "keyboard_names.json"     # our own labels for macros and advanced keys
        self.definition = via.load_definition(*definition_paths())
        self._opener = opener or (lambda: via.Keyboard.open(self.definition))
        self._log = log or (lambda message: None)
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.simulated = simulated          # --fake: a simulated keyboard, never the user's

    def _with(self, fn):  # noqa: ANN001, ANN202
        with self._lock:
            kb = self._opener()
            try:
                result = fn(kb)
                self.last_error = None
                return result
            finally:
                kb.close()

    # --- reads ---

    def state(self) -> dict:
        def read(kb: via.Keyboard) -> dict:
            layers = kb.layer_count()
            data = {"connection": kb.connection, "protocol": kb.protocol(), "layers": layers,
                    "rows": kb.rows, "cols": kb.cols, "keymap": kb.keymap(layers), "encoders": kb.encoders(layers),
                    "lighting": kb.lighting(), "system": kb.system(), "battery": kb.battery()}
            data.update(self._extras(kb))
            self._ensure_backup(data)
            data.pop("macro_bytes", None)
            return data
        data = self._with(read)
        codes = {c for layer in data["keymap"] for row in layer for c in row}
        codes |= {v for layer in data["encoders"] for knob in layer for v in knob.values()}
        codes |= {td[f] for td in data.get("tap_dances") or [] for f in via.TD_FIELDS}
        data["names"] = {str(c): keycodes.describe(c) for c in sorted(codes)}
        data["layout"] = self.layout()
        data["backup"] = self.backup_path.exists()
        data["backup_time"] = None
        if data["backup"]:
            try:
                data["backup_time"] = json.loads(self.backup_path.read_text(encoding="utf-8")).get("time")
            except (OSError, ValueError):
                pass
        data["labels"] = self.labels()
        data["simulated"] = self.simulated
        return data

    @staticmethod
    def _extras(kb: via.Keyboard) -> dict:
        """Macros and advanced keys; None each when the keyboard does not answer them (they are not essential)."""
        out: dict = {"macros": None, "macro_space": None, "macro_bytes": None, "tap_dances": None}
        try:
            count, size = kb.macro_info()
            raw = kb.macro_bytes()
            out["macros"] = via.parse_macros(raw, count)
            out["macro_bytes"] = raw.hex()
            out["macro_space"] = {"count": count, "size": size, "used": len(via.encode_macros(out["macros"]))}
        except (via.ViaError, ValueError):
            pass
        try:
            out["tap_dances"] = kb.tap_dances()
        except (via.ViaError, ValueError):
            pass
        return out

    def labels(self) -> dict:
        try:
            data = json.loads(self.names_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"macros": dict(data.get("macros") or {}), "tap_dances": dict(data.get("tap_dances") or {})}

    def set_label(self, kind: str, index: int, name: str) -> dict:
        if kind not in ("macros", "tap_dances"):
            raise ValueError("kind is macros or tap_dances")
        data = self.labels()
        name = str(name).strip()[:24]
        if name:
            data[kind][str(index)] = name
        else:
            data[kind].pop(str(index), None)
        self.names_path.parent.mkdir(parents=True, exist_ok=True)
        self.names_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def layout(self) -> list[dict] | None:
        """Key positions from the official definition (KLE rows): [{row, col, x, y, w, h, encoder?}]."""
        if not self.definition:
            return None
        return kle_keys(self.definition["layouts"]["keymap"])

    def battery(self) -> dict | None:
        return self._with(lambda kb: kb.battery())

    @staticmethod
    def catalog() -> dict:
        return {"keycodes": keycodes.catalog(),
                "effects": [{"id": k, "name": v, "speed": k not in via.NO_SPEED, "color": k not in via.NO_COLOR}
                            for k, v in via.EFFECTS.items()],
                "system": [{"item": item, "label": label, "options": [{"value": v, "label": t} for v, t in opts.items()]}
                           for item, (label, opts) in via.SYSTEM_ITEMS.items()],
                "knobs": [{"encoder": 1, "label": "屏幕旋钮"}, {"encoder": 0, "label": "音量旋钮"}]}

    # --- writes ---

    def set_key(self, layer: int, row: int, col: int, keycode: int) -> dict:
        self._backup_first()
        value = self._with(lambda kb: kb.set_keycode(layer, row, col, keycode))
        self._log(f"keyboard: layer {layer} key {row},{col} = {keycodes.describe(value)['label']}")
        return keycodes.describe(value)

    def set_knob(self, layer: int, encoder: int, gesture: str, keycode: int) -> dict:
        """gesture: cw, ccw (the encoder) or press (its matrix key)."""
        self._backup_first()
        if gesture == "press":
            row, col = via.KNOB_PRESS[encoder]
            value = self._with(lambda kb: kb.set_keycode(layer, row, col, keycode))
        elif gesture in ("cw", "ccw"):
            value = self._with(lambda kb: kb.set_encoder(layer, encoder, gesture == "cw", keycode))
        else:
            raise ValueError("gesture is cw, ccw or press")
        self._log(f"keyboard: layer {layer} knob {encoder} {gesture} = {keycodes.describe(value)['label']}")
        return keycodes.describe(value)

    def set_lighting(self, changes: dict) -> dict:
        allowed = {k: changes[k] for k in ("brightness", "effect", "speed", "hue", "saturation") if k in changes}
        self._backup_first()
        return self._with(lambda kb: kb.set_lighting(**allowed))

    def set_system(self, item: int, value: int) -> int:
        self._backup_first()
        return self._with(lambda kb: kb.set_system(item, value))

    def set_macros(self, macros: list) -> dict:
        if not isinstance(macros, list) or not all(isinstance(m, list) for m in macros):
            raise ValueError("macros is a list of action lists")
        self._backup_first()

        def write(kb: via.Keyboard) -> dict:
            result = kb.set_macros(macros)
            count, size = kb.macro_info()
            return {"macros": result, "macro_space": {"count": count, "size": size,
                                                       "used": len(via.encode_macros(result))}}
        out = self._with(write)
        self._log(f"keyboard: macros written ({out['macro_space']['used']} of {out['macro_space']['size']} bytes)")
        return out

    def set_tap_dance(self, index: int, entry: dict) -> dict:
        self._backup_first()
        args = {f: int(entry.get(f) or 0) for f in via.TD_FIELDS}
        args["term"] = int(entry.get("term", 200))
        td = self._with(lambda kb: kb.set_tap_dance(index, **args))
        self._log(f"keyboard: advanced key {index + 1} written")
        names = {str(td[f]): keycodes.describe(td[f]) for f in via.TD_FIELDS}
        return {**td, "names": names}

    def restore(self) -> dict:
        """Write the backup back: keymap, knobs, lighting and system settings."""
        backup = json.loads(self.backup_path.read_text(encoding="utf-8"))

        def write(kb: via.Keyboard) -> int:
            changed = 0
            now = kb.keymap(len(backup["keymap"]))
            for l, layer in enumerate(backup["keymap"]):
                for r, row in enumerate(layer):
                    for c, code in enumerate(row):
                        if now[l][r][c] != code:
                            allow = {code} if keycodes.forbidden(code) else set()
                            kb.set_keycode(l, r, c, code, allow=allow)
                            changed += 1
            for l, layer in enumerate(backup["encoders"]):
                for e, knob in enumerate(layer):
                    for gesture in ("cw", "ccw"):
                        if kb.encoder(l, e, gesture == "cw") != knob[gesture]:
                            kb.set_encoder(l, e, gesture == "cw", knob[gesture])
                            changed += 1
            light = backup["lighting"]
            kb.set_lighting(**light)
            for item, value in backup["system"].items():
                if int(item) in via.SYSTEM_ITEMS:
                    kb.set_system(int(item), value)
            if backup.get("macro_bytes") is not None and kb.macro_bytes().hex() != backup["macro_bytes"]:
                kb.set_macro_bytes(bytes.fromhex(backup["macro_bytes"]).rstrip(b"\x00"))
                changed += 1
            for td in backup.get("tap_dances") or []:
                if kb.tap_dance(td["index"]) != td:
                    kb.set_tap_dance(td["index"], **{k: td[k] for k in (*via.TD_FIELDS, "term")})
                    changed += 1
            return changed
        changed = self._with(write)
        self._log(f"keyboard: restored the backup ({changed} keys/knob slots changed)")
        return {"changed": changed}

    # --- backup ---

    def _ensure_backup(self, data: dict) -> None:
        if self.backup_path.exists():
            # backups from before macros and advanced keys: add them the first time they can be read
            try:
                backup = json.loads(self.backup_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            missing = {k: data[k] for k in ("macro_bytes", "tap_dances") if k not in backup and data.get(k) is not None}
            if missing:
                backup.update(missing)
                self._write_backup(backup)
            return
        backup = {k: data[k] for k in ("keymap", "encoders", "lighting", "system")}
        backup.update({k: data[k] for k in ("macro_bytes", "tap_dances") if data.get(k) is not None})
        backup["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_backup(backup)
        self._log(f"keyboard: backup saved to {self.backup_path}")

    def _write_backup(self, backup: dict) -> None:
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.backup_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.backup_path)

    def _backup_first(self) -> None:
        if not self.backup_path.exists():
            self.state()


def kle_keys(rows: list) -> list[dict]:
    """Key rectangles with their matrix position from the KLE rows of a VIA definition, parsed with pykle_serial
    (MIT, a port of keyboard-layout-editor's kle-serial, which the VIA app uses too). As in VIA, the top-left
    label is "row,col" and label 4 is "e<n>" for an encoder."""
    import pykle_serial
    keys = []
    for key in pykle_serial.deserialize(rows).keys:
        labels = key.labels
        pos = (labels[0] or "").strip()
        if "," not in pos:
            continue
        r, c = (int(v) for v in pos.split(",")[:2])
        item = {"row": r, "col": c, "x": key.x, "y": key.y, "w": key.width, "h": key.height}
        encoder = labels[4] if len(labels) > 4 else None
        if encoder and encoder.startswith("e"):
            item["encoder"] = int(encoder[1:] or 0)
        keys.append(item)
    return keys
