// Tape transport for the cassette pages: turns the media state into how far the tape has run (--p, 0..1),
// the hub angle (--spin) and whether the deck is rewinding.
//  - With a duration the tape follows the song: full at the start, the left reel empty at the end.
//  - Without one (some players report no duration) the tape plays a virtual side of LOOP_MS and rewinds.
//  - Whenever the tape has to go back (next song, seek backwards, end of the virtual side) it rewinds
//    visibly, hubs running backwards fast, instead of jumping.
const tapeDeck = (() => {
  const LOOP_MS = 90000, REWIND_MS = 4000;  // virtual side length; a full rewind
  let shown = null, virt = 0, turn = 0, last = 0, rewinding = false;
  return (data) => {
    const now = data.now || Date.now(), dt = last ? Math.min(1000, Math.max(0, now - last)) : 0;
    last = now;
    const playing = data.has_media && data.playing;
    let target;
    if (data.dur) target = Math.min(1, Math.max(0, data.pos / data.dur));
    else {
      if (playing && !rewinding) virt += dt / LOOP_MS;
      if (virt >= 1) virt = 0;  // side over: back to the start, rewound below
      target = virt;
    }
    if (shown === null) shown = target;
    rewinding = target < shown - 0.02;
    if (rewinding) {
      shown = Math.max(target, shown - dt / REWIND_MS);
      turn -= dt * 1.2;
    } else {
      shown = target;
      if (playing) turn += dt * 0.12;
    }
    turn %= 360;
    return { p: shown, spin: turn, rewinding };
  };
})();
