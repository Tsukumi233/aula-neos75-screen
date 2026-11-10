/* Freestanding Thumb-2 runtime. All protocol and display work runs in GUI context. */
#include <stdint.h>
#include <stddef.h>

#define RX_CAP 0x2a304u
#define RX ((uint8_t *)0x800d4200u)
#define CANVAS_W 480u
#define CANVAS_H 360u
#define RGB_BASE 0x800fe604u
#define RGB_BYTES (CANVAS_W * CANVAS_H * 2u)
/* The borrowed pool (capacity 0x7e900) behind the full canvas: region frames decode at its start, the
 * rest caches one JPEG per host page (shown at once when the knob turns to that page).
 */
#define REGION_RGB (RGB_BASE + RGB_BYTES)
#define REGION_BYTES 81920u
#define CACHE_BASE (REGION_RGB + REGION_BYTES)
#define CACHE_BYTES (0x7e900u - RGB_BYTES - REGION_BYTES)
#define CACHE_SLOTS 8u
#define TICK_ACTIVE 5u     /* GUI timer period while the slot is shown (stock: 10 ms) */
#define TICK_STOCK 10u
#define RING 32u
#define TX_RING 32u
#define HOST_TIMEOUT 10000u
#define FN(a, t) ((t)((a) | 1u))
#define U8(a) (*(volatile uint8_t *)(a))
#define U32(a) (*(volatile uint32_t *)(a))
#define BARRIER() __asm volatile("dmb" ::: "memory")

typedef struct { uint32_t header, size, data; } Image;
typedef struct {
    uint32_t magic;
    volatile uint32_t head, tail, overflow, route;
    uint8_t reports[RING][64];
    uint32_t tx_head, tx_tail;
    uint8_t tx[TX_RING][64];
    volatile uint32_t active;
    uint32_t state, type, total, received, crc, block;
    uint32_t ok, errors, seq, last_error, decode_ms;
    uint32_t width, height, jpeg_len;
    uint32_t screen, image, previous, rgb, copy_offset;
    uint32_t boot_tick, confirmed, boot_seen, gui_ticks, frame_tick, restoring;
    Image descriptor;
    uint32_t armed;
    volatile uint32_t host_tick;
    uint32_t previous_page, discard;
    uint32_t pages, page;   /* host pages in the slot (sub 06; 0 = 1) and the one shown */
    uint32_t canvas;        /* a full 480x360 frame is shown: region frames may update it */
    uint32_t region, rx, ry, rw, rh;   /* region: 0 full frame, 1 region frame, 2 cache upload */
    uint32_t cache_len[CACHE_SLOTS], cache_crc[CACHE_SLOTS], cache_page;
    uint32_t frame_tag;     /* start byte 8: 0 untagged, else 1 + the host page the frame shows */
    uint32_t takeover;      /* sub 07: knob turns only reported (21), never move a page */
    uint32_t pass;          /* steps the host asked for (sub 30) still to run as plain turns */
} Runtime;

Runtime rt;

/* Trampolines replay the displaced, position-independent prologue. */
extern void stock_ba(const uint8_t *, uint32_t, uint32_t);
extern uint32_t stock_usb(uint32_t, const uint8_t *, uint32_t);
extern void stock_poll(void *);
extern void stock_static(void *);
extern uint32_t stock_anim(uint32_t);
extern uint32_t stock_resource(const void *);
extern void stock_page(uint32_t);

static void zero(void *p, uint32_t n) {
    uint8_t *d = p;
    while (n--) *d++ = 0;
}
static void copy(void *dst, const void *src, uint32_t n) {
    uint8_t *d = dst; const uint8_t *s = src;
    while (n--) *d++ = *s++;
}
static uint32_t be16(const uint8_t *p) { return (uint32_t)p[0] * 256 + p[1]; }
static uint32_t be32(const uint8_t *p) { return (be16(p) << 16) | be16(p+2); }
static uint32_t le32(const uint8_t *p) {
    return p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static void put16(uint8_t *p, uint32_t x) { p[0]=x; p[1]=x>>8; }
static void put32(uint8_t *p, uint32_t x) { put16(p,x); put16(p+2,x>>16); }
static uint32_t ticks(void) { return U32(0x20003a54); }

/* Replace the stock confirmation init entry with explicit runtime initialization. */
uint32_t rt_init(void) {
    zero(&rt, sizeof(rt));
    rt.magic = 0x3154534e;
    return 0;
}

/* Carve known external RAM from the stock GUI heap, preserving its upper bound. */
uint32_t rt_heap(uint32_t base, uint32_t size) {
    return FN(0x2c88c,uint32_t (*)(uint32_t,uint32_t))(base+0x1200,size-0x1200);
}

static void enqueue(const uint8_t *p, uint32_t n) {
    uint32_t h = rt.head;
    if (n != 64 || h - rt.tail >= RING) { rt.overflow = 1; return; }
    copy(rt.reports[h & (RING-1)], p, 64);
    BARRIER(); rt.head = h+1;
}

/* HID producer: bounded report copy and flags only, with no stock upload calls. */
void rt_ba(const uint8_t *p, uint32_t n, uint32_t source) {
    if (rt.magic != 0x3154534e) return;
    if (source!=0xc) { if (!rt.active) stock_ba(p,n,source); return; }
    if (n >= 8 && p[0] == 0xba && p[1] == 6 && p[2] == 0xaa) {
        if (p[3] == 0x5a || p[3] == 0x5b) rt.route = 1;
        else if (!rt.active) rt.route = 0;
    }
    if (rt.route) rt.host_tick=ticks();
    if (rt.route || rt.active) enqueue(p,n);
    else stock_ba(p,n,source);
}
uint32_t rt_usb(uint32_t context, const uint8_t *p, uint32_t n) {
    if (p && n >= 2 && p[0] == 0xa5 && p[1] == 0x3b) {
        rt.host_tick=ticks();
        enqueue(p,n); return 0;
    }
    return stock_usb(context,p,n);
}

static uint8_t *new_tx(void) {
    if (rt.tx_head - rt.tx_tail >= TX_RING) return 0;
    uint8_t *p = rt.tx[rt.tx_head & (TX_RING-1)];
    zero(p,64); return p;
}
static void commit_tx(void) { rt.tx_head++; }
static void checksum(uint8_t *p, uint32_t end) {
    uint32_t s=0;
    for (uint32_t i=1; i<end; i++) s+=p[i];
    p[end]=(uint8_t)~s;
}
static void reply(const uint8_t *fields, uint32_t n) {
    uint8_t *p=new_tx();
    if (!p || n>57) return;
    p[0]=0xa5; p[1]=0x3b; p[3]=n+1;
    copy(p+5,fields,n); checksum(p,5+n); commit_tx();
}
static void visibility_event(uint32_t reason) {
    uint8_t f[3]={0x20,rt.active,reason}; reply(f,3);
}
static uint32_t page_count(void) { return rt.pages ? rt.pages : 1; }
static void page_event(void) {
    uint8_t f[3]={0x22,rt.page,page_count()}; reply(f,3);
}
static uint32_t slot_bytes(void) { return (CACHE_BYTES/page_count()) & ~3u; }
static uint8_t *slot(uint32_t page) { return (uint8_t *)(CACHE_BASE+page*slot_bytes()); }
static void forget_cache(void) { for (uint32_t i=0;i<CACHE_SLOTS;i++) rt.cache_len[i]=0; }
static void ack(uint32_t first) {
    uint8_t *p=new_tx(); if (!p) return;
    p[0]=0xba; p[1]=7; p[2]=0xa5; p[3]=rt.type;
    p[5]=2; p[6]=0xfe; p[7]=first;
    uint32_t s=p[3]+p[4]+p[5]+p[6]+p[7]; p[8]=(uint8_t)~s;
    commit_tx();
}
void rt_event(uint32_t status, uint32_t ms) {
    uint8_t f[6];
    rt.seq=(rt.seq+1)&65535;
    if (status==1) rt.ok++; else rt.errors++;
    rt.last_error = status==1 ? 0 : status; rt.decode_ms=ms>65535?65535:ms;
    f[0]=0x10; f[1]=status; put16(f+2,rt.seq); put16(f+4,rt.decode_ms);
    reply(f,6);
}
static void fail(uint32_t status) {
    rt.state=0; rt.total=rt.received=0; rt_event(status,0);
}

/* Reflected zlib CRC-32, maintained per full 4096-byte block. */
static uint32_t crc_update(uint32_t crc, const uint8_t *p, uint32_t n) {
    while (n--) {
        crc^=*p++;
        for (uint32_t j=0;j<8;j++) crc=(crc>>1) ^ (0xedb88320u & (0u-(crc&1u)));
    }
    return crc;
}
void rt_receive(const uint8_t *p) {
    uint32_t n=p[1];
    if (p[0]!=0xba) return;
    if (n==6 && p[2]==0xaa && (p[3]==0x5a || p[3]==0x5b || p[3]==0xb0 || p[3]==0xfc)) {
        if (p[3]!=0x5a && p[3]!=0x5b) { rt_event(4,0); return; }
        if (!rt.active && !rt.armed) { rt_event(5,0); return; }
        /* Busy only while a complete frame is waiting for or in decode. A new start
         * while still receiving means the host abandoned that stream (it gave up
         * waiting); replace it without an event, so every frame-done the host sees
         * after its start belongs to that start. Hardware 2026-11-07.
         */
        if (rt.state>=2) { rt_event(4,0); return; }
        if (rt.state) rt.errors++;
        uint32_t total=be32(p+4);
        if (total<9 || total>RX_CAP) { rt_event(3,0); return; }
        rt.discard=!rt.active;
        rt.frame_tag=p[8];
        rt.type=p[3]; rt.total=total; rt.received=rt.block=0;
        rt.crc=0xffffffff; rt.state=1; ack(1); return;
    }
    if (rt.state!=1) return;
    if (!n || n>62) { fail(3); return; }
    uint32_t remain=rt.total-rt.received, room=4096-rt.block;
    if (n>room || n>remain) { fail(3); return; }
    uint32_t crc=crc_update(rt.crc,p+2,n);
    if (n==room) {
        /* Stock n counts data only; the CRC follows outside the length field. */
        if (n>58) { fail(3); return; }
        if ((crc^0xffffffff)!=le32(p+2+n)) { fail(3); return; }
    }
    /* Hidden streams retain stock ACK flow without borrowing any pixel/input RAM. */
    if (!rt.discard) copy(RX+rt.received,p+2,n);
    rt.crc=crc;
    rt.received+=n; rt.block+=n;
    if (rt.block==4096) { rt.block=0; rt.crc=0xffffffff; ack(0); }
    if (rt.received==rt.total) {
        if (rt.discard) { rt.state=0; rt_event(5,0); }
        else rt.state=2;
    }
}

/* Strict bounded baseline grammar protects the vendor's less defensive parser. */
uint32_t rt_jpeg(const uint8_t *p, uint32_t n, uint32_t w, uint32_t h) {
    if (n<4 || p[0]!=255 || p[1]!=0xd8 || p[n-2]!=255 || p[n-1]!=0xd9) return 0;
    uint32_t pos=2, sof=0, qt=0, dc=0, ac=0, dri=0;
    while (pos+4<=n) {
        if (p[pos++]!=255) return 0;
        uint32_t marker=p[pos++];
        uint32_t len=be16(p+pos);
        if (len<2 || len>n-pos) return 0;
        const uint8_t *q=p+pos+2; uint32_t size=len-2;
        if (marker==0xc0) {
            if (sof || size!=15 || q[0]!=8 || be16(q+1)!=h || be16(q+3)!=w || q[5]!=3
                || q[6]!=1 || (q[7]!=0x11 && q[7]!=0x21 && q[7]!=0x22) || q[8]!=0
                || q[9]!=2 || q[10]!=0x11 || q[11]!=1
                || q[12]!=3 || q[13]!=0x11 || q[14]!=1) return 0;
            sof=1;
        } else if (marker==0xdb) {
            uint32_t k=0;
            while (k<size) {
                if (size-k<65 || q[k]>1) return 0;
                qt|=1u<<q[k++];
                for (uint32_t j=0;j<64;j++) if (!q[k++]) return 0;
            }
        } else if (marker==0xc4) {
            uint32_t k=0;
            while (k<size) {
                if (size-k<17) return 0;
                uint32_t id=q[k++], count=0, slots=1;
                if (id!=0 && id!=1 && id!=0x10 && id!=0x11) return 0;
                for (uint32_t j=0;j<16;j++) {
                    slots*=2;
                    if (q[k+j]>=slots) return 0;
                    slots-=q[k+j]; count+=q[k+j];
                }
                k+=16;
                if (!count || count>256 || count>size-k) return 0;
                uint32_t seen[8]; zero(seen,sizeof(seen));
                for (uint32_t j=0;j<count;j++) {
                    uint32_t v=q[k+j];
                    if (!(id&16) ? v>11 : ((v&15)>10 || (!(v&15) && v!=0 && v!=0xf0))) return 0;
                    if (seen[v>>5] & (1u<<(v&31))) return 0;
                    seen[v>>5]|=1u<<(v&31);
                }
                k+=count;
                if (id&16) ac|=1u<<(id&1); else dc|=1u<<id;
            }
        } else if (marker==0xdd) {
            if (size!=2 || !be16(q)) return 0;
            dri=1;
        } else if (marker==0xda) {
            if (!sof || qt!=3 || dc!=3 || ac!=3 || size!=10 || q[0]!=3
                || q[1]!=1 || q[2]!=0 || q[3]!=2 || q[4]!=0x11 || q[5]!=3
                || q[6]!=0x11 || q[7]!=0 || q[8]!=63 || q[9]!=0) return 0;
            pos+=len;
            if (pos>=n-2) return 0;
            while (pos<n-2) {
                if (p[pos++]==255) {
                    if (pos>=n-2) return 0;
                    uint32_t v=p[pos++];
                    if (v && (!dri || v<0xd0 || v>0xd7)) return 0;
                }
            }
            return pos==n-2;
        } else if (marker==0xe0) {
            if (size<14 || q[0]!='J' || q[1]!='F' || q[2]!='I' || q[3]!='F' || q[4]
                || size!=14u+3u*q[12]*q[13]) return 0;
        } else if (marker!=0xfe) return 0;
        pos+=len;
    }
    return 0;
}

/* 'U','R', w, h, x, y (u16 BE), jpeg_len (u32 BE), JPEG: replaces a w x h rectangle of the shown
 * full frame. w and h are multiples of 16 (whole JPEG MCUs, no decoder padding), x and y even.
 */
static uint32_t validate_region(void) {
    if (rt.total<19) return 3;
    uint32_t w=be16(RX+2), h=be16(RX+4), x=be16(RX+6), y=be16(RX+8), n=be32(RX+10);
    if (!w || !h || (w&15) || (h&15) || (x&1) || (y&1) || x+w>CANVAS_W || y+h>CANVAS_H
        || w*h*2>REGION_BYTES || n!=rt.total-14) return 3;
    if (!rt.canvas) return 6;
    rt.region=1; rt.rx=x; rt.ry=y; rt.rw=w; rt.rh=h; rt.jpeg_len=n;
    return rt_jpeg(RX+14,n,w,h) ? 0 : 2;
}
/* 'U','C', page, 0, jpeg_len (u32 BE), JPEG of the whole 480x360 canvas: stored, not shown. */
static uint32_t validate_cache(void) {
    if (rt.total<17) return 3;
    uint32_t page=RX[2], n=be32(RX+4);
    if (RX[3] || page>=page_count() || n!=rt.total-8 || n>slot_bytes()) return 3;
    rt.region=2; rt.cache_page=page; rt.jpeg_len=n;
    return rt_jpeg(RX+8,n,CANVAS_W,CANVAS_H) ? 0 : 2;
}
uint32_t rt_validate(void) {
    rt.region=0;
    if (rt.total<9 || rt.total>RX_CAP || rt.received!=rt.total) return 3;
    if (rt.type==0x5a && RX[0]==0x55 && RX[1]==0x52) return validate_region();
    if (rt.type==0x5a && RX[0]==0x55 && RX[1]==0x43) return validate_cache();
    uint32_t w=be16(RX+2), h=be16(RX+4);
    if (!w || !h || w>CANVAS_W || h>CANVAS_H || ((w|h)&1) || RX[6] || RX[7]!=1 || RX[8]) return 3;
    rt.width=w; rt.height=h;
    if (rt.type==0x5b) return RX[0]==0x57 && RX[1]==0x51 && rt.total==9+w*h*2 ? 0 : 3;
    if (rt.type!=0x5a || RX[0]!=0x55 || RX[1]!=0x42 || rt.total<17) return 3;
    uint32_t n=be32(RX+9);
    if (n!=rt.total-13) return 3;
    rt.jpeg_len=n;
    return rt_jpeg(RX+13,n,w,h) ? 0 : 2;
}

static uint32_t enter(void) {
    if (rt.active) return 0;
    /* Do not steal the scratch area from an in-progress stock transfer. */
    if (U32(0x20004758) || U32(0x20004768)) return 4;
    /* Borrow the fixed resource JPEG pool (capacity 0x7e900), never static RGB. */
    rt.rgb=RGB_BASE;
    if (!rt.screen) {
        rt.screen=FN(0x26dd4,uint32_t (*)(uint32_t))(0);
        if (!rt.screen) return 3;
        FN(0x2a770,void (*)(uint32_t,uint32_t,uint32_t))(rt.screen,CANVAS_W,CANVAS_H);
        /* Explicit zero insets keep child coordinates relative to canvas (0,0). */
        FN(0x2aa60,void (*)(uint32_t))(rt.screen);
        FN(0x2a744,void (*)(uint32_t,uint32_t))(rt.screen,0);
        FN(0x2a9d4,void (*)(uint32_t,uint32_t,uint32_t))(rt.screen,255,0);
        FN(0x2a9b4,void (*)(uint32_t,uint32_t,uint32_t))(rt.screen,0,0);
        FN(0x2a9c4,void (*)(uint32_t,uint32_t,uint32_t))(rt.screen,0,0);
    }
    if (!rt.image) {
        rt.image=FN(0x2412c,uint32_t (*)(uint32_t))(rt.screen);
        if (!rt.image) return 3;
        FN(0x2a6e8,void (*)(uint32_t,uint32_t,uint32_t))(rt.image,0,0);
        FN(0x2a770,void (*)(uint32_t,uint32_t,uint32_t))(rt.image,CANVAS_W,CANVAS_H);
    }
    /* A previous session's descriptor must not expose borrowed resource pixels. */
    FN(0x26798,void (*)(uint32_t,uint32_t))(rt.image,1);
    rt.canvas=0;
    rt.previous=FN(0x2b114,uint32_t (*)(void))();
    rt.previous_page=U8(0x200044ad);
    /* A stream started while hidden is being discarded; end it explicitly so the
     * host does not wait for ACKs that the reset below would otherwise swallow.
     */
    if (rt.state) rt_event(5,0);
    rt.state=0;
    rt.discard=0;
    rt.restoring=0;
    /* Guards become active before loading a screen or accepting frame packets. */
    rt.armed=1; rt.active=1; BARRIER();
    FN(0x2b120,void (*)(uint32_t))(rt.screen);
    return 0;
}
static void hide(void) {
    if (rt.active) {
        if (rt.state) rt_event(5,0);
        rt.state=rt.total=rt.received=0;
        /* Keep the scope through the first stock poll after screen restoration.
         * That poll updates the live status UI before attempting to persist it.
         */
        rt.restoring=2;
        FN(0x26798,void (*)(uint32_t,uint32_t))(rt.image,1);
        rt.canvas=0;   /* stock code may reuse the pool while hidden */
        /* Close cached file handles, not just the resource decoder's cookie.
         * The next stock draw must reopen and regenerate the borrowed pixels.
         * Static/animation RGB was never modified, so no settings path is used.
         */
        FN(0x23fa0,void (*)(uint32_t))(0);
        FN(0x235d4,void (*)(void))();
        U32(0x2000bd94)=0;
        BARRIER(); rt.active=0;
    }
}
static void restore_previous(void) {
    /* Neither the virtual slot nor this passive restoration creates a page-save timer.
     * A pending save from an earlier genuine stock selection remains untouched.
     */
    rt.restoring=2;
    U8(0x200044ad)=rt.previous_page;
    FN(0x2b120,void (*)(uint32_t))(rt.previous);
    FN(0x29558,void (*)(uint32_t))(rt.previous);
}
static uint32_t leave(void) {
    /* Only a visible slot is replaced by its entry page. When hidden, the user
     * is already on a stock page of their own choosing; leave it in place
     * (same rule as the hidden timeout).
     */
    uint32_t was_visible=rt.active;
    hide();
    if (was_visible) {
        if (rt.state) rt_event(5,0);
        rt.state=rt.total=rt.received=0;
        restore_previous();
    }
    rt.armed=0; rt.takeover=rt.pass=0;
    return 0;
}
/* The original switch (0x3C128) stops the outgoing page's animation only when
 * that page is the active screen. Leaving the slot, our screen is active, so do
 * the same stop for the page that was shown before the slot. Otherwise the page
 * load functions (0x117D8/0x11ABC) zero the timer handle without deleting the
 * timer and create another one: every round trip added a live timer (faster
 * animation, leaked LVGL memory). Hardware 2026-11-07.
 */
static void stop_previous_animation(void) {
    uint32_t prev=rt.previous;
    if (prev && prev==U32(0x200034ac) && U32(0x200034d8)) FN(0x33f64,void (*)(uint32_t))(0x2000349c);
    if (prev && prev==U32(0x20003544) && U32(0x20003564)) FN(0x33f80,void (*)(void))();
    if (prev && prev==U32(0x20003580) && U32(0x200035a0)) FN(0x33f88,void (*)(uint32_t))(0x2000349c);
}
/* Called only by the original GUI input callback (BL at 0x1d268).
 * UART/GPIO producers continue to use the original bounded key-message queue.
 */
static void show_cached(void);
void rt_key(uint32_t code) {
    uint32_t dir=(code==0x12 || code==0x13)?1:(code==0x11 || code==0x14)?255:0;
    if (!rt.armed || !dir) {
        FN(0x1d094,void (*)(uint32_t))(code); return;
    }
    uint8_t f[3]={0x21,dir,1};
    /* Takeover (sub 07): the host maps the turn to its own action, so it is only
     * reported, marked taken (third byte 1). A step the host asked for (sub 30,
     * e.g. its "next page" action) runs as a plain turn and is not reported, or
     * the host would map it again.
     */
    if (rt.takeover) {
        if (!rt.pass) { reply(f,3); return; }
        rt.pass--;
    } else reply(f,2);
    /* Match the original page gate, including its context-null exception.
     * The key handler has a weaker early gate; passing the original key here
     * also preserves its RAM-only index change when page switching is blocked.
     */
    if ((!(U8(0x2000bf77)&1) && U32(0x2000349c)) || U8(0x200044ae)) {
        FN(0x1d094,void (*)(uint32_t))(code); return;
    }
    if (rt.active) {
        /* Host pages (sub 06) behave like stock pages: a step moves between
         * them and only a step past either end leaves for the stock neighbour.
         * The last frame stays up until the host sends the new page's frame.
         */
        uint32_t next=dir==1 ? rt.page+1 : rt.page-1;
        if (next<page_count()) { rt.page=next; show_cached(); page_event(); return; }
        hide();
        stop_previous_animation();
        /* The slot has no factory index. Seed the predecessor and let the
         * original key and page code select/save the user's genuine neighbour.
         */
        U8(0x200044ad)=dir==1?5:0;
        FN(0x1d094,void (*)(uint32_t))(code);
        visibility_event(1);
    } else if ((dir==1 && U8(0x200044ad)==5) || (dir==255 && U8(0x200044ad)==0)) {
        if (!enter()) {
            /* Clockwise from stock page 6 lands on the first host page,
             * counter-clockwise from page 1 on the last. */
            rt.page=dir==1 ? 0 : page_count()-1;
            show_cached();
            if (page_count()>1) page_event();
            visibility_event(1);
        } else FN(0x1d094,void (*)(uint32_t))(code);
    } else FN(0x1d094,void (*)(uint32_t))(code);
}
void rt_static_save(uint32_t key, uint32_t value) {
    if (rt.restoring!=1) FN(0x17a1c,void (*)(uint32_t,uint32_t))(key,value);
}
void rt_status_save(const void *data, uint32_t key, uint32_t size) {
    /* Only the passive status-cache save at 0x3379c is redirected here.
     * The stock updater already refreshed its last-drawn RAM state, so later
     * unchanged timer polls will not retry the omitted persistence operation.
     */
    if (rt.restoring==2 && data==(const void *)0x2000466d && key==0x11 && size==2) return;
    FN(0x179d8,void (*)(const void *,uint32_t,uint32_t))(data,key,size);
}
void rt_control(const uint8_t *p) {
    if (p[0]!=0xa5 || p[1]!=0x3b) return;
    uint32_t n=p[3];
    if (p[2] || p[4] || n<2 || n>59) return;
    uint32_t sum=0;
    for (uint32_t i=1;i<4+n;i++) sum+=p[i];
    if ((uint8_t)~sum!=p[4+n]) return;
    rt.host_tick=ticks();
    uint8_t f[32]; zero(f,32); f[0]=p[5];
    /* HELLO flags bit 0: a knob step from the PC (desktop app, CLI, unattended
     * hardware tests). It enters the stock key queue exactly as the UART `A5 39 01/02`
     * handler does (0x35220 -> table 0x433EE -> producer 0x1CD4C), so the queue,
     * the GUI read callback, rt_key and the stock page code all run for real.
     * Like a real turn onto a stock page, it leads to the stock 2 s page save.
     */
    if (f[0]==0x30 && n==3) {
        uint8_t d=p[6];
        if (rt.takeover && (d==1 || d==0xff) && rt.pass<8) rt.pass++;
        if (d==1) FN(0x1cd4c,void (*)(uint32_t,uint32_t))(2,0);
        else if (d==0xff) FN(0x1cd4c,void (*)(uint32_t,uint32_t))(1,0);
        f[1]=(d==1 || d==0xff)?0:0xee; reply(f,2); return;
    }
    if (f[0]==7 && n==3) {
        /* Knob takeover while the session lasts: 1 = turns are only reported (event 21),
         * 0 = stock paging. Ends with the session (LEAVE, host timeout). RAM only. */
        uint32_t on=p[6];
        if (on>1 || !rt.armed) { f[1]=0xee; f[2]=rt.takeover; reply(f,3); return; }
        rt.takeover=on; if (!on) rt.pass=0;
        f[1]=0; f[2]=on; reply(f,3); return;
    }
    if (f[0]==6 && n==3) {
        /* Number of host pages the knob steps through inside the slot (1..8). */
        uint32_t count=p[6];
        if (count<1 || count>8) { f[1]=0xee; reply(f,2); return; }
        if (count!=page_count()) forget_cache();   /* slot sizes change */
        rt.pages=count; if (rt.page>=count) rt.page=count-1;
        f[1]=0; f[2]=rt.page; f[3]=count; put32(f+4,slot_bytes()); reply(f,8); return;
    }
    if (n!=2) { f[1]=0xee; reply(f,2); return; }
    if (f[0]==1) {
        f[1]='N'; f[2]='S'; f[3]=2; put16(f+4,3);
        put16(f+6,CANVAS_W); put16(f+8,CANVAS_H);
        put16(f+10,CANVAS_W); put16(f+12,CANVAS_H);
        f[14]=3; put32(f+15,RX_CAP); f[19]=1;
        /* flags bit 1: keyboard telemetry (sub 05); bit 2: host pages (sub 06);
         * bit 3: region frames ('UR'); bit 4: page cache ('UC'), pipelined starts, 5 ms tick;
         * bit 5: page-tagged frames (BA start byte 8); bit 6: knob takeover (sub 07) */
        f[20]=127;  /* flags bit 0: knob step from the PC (sub 0x30) */
        put16(f+21,HOST_TIMEOUT); reply(f,23);
    } else if (f[0]==2 || f[0]==3) {
        uint32_t visible=rt.active;
        f[1]=f[0]==2?enter():leave(); reply(f,2);
        if (visible!=rt.active) visibility_event(2);
    } else if (f[0]==4) {
        f[1]=rt.active ? rt.state+1 : 0; put32(f+2,rt.ok); put32(f+6,rt.errors);
        f[10]=rt.last_error; put16(f+11,rt.decode_ms);
        f[13]=rt.active; f[14]=rt.armed; f[15]=rt.page; f[16]=page_count(); reply(f,17);
    } else if (f[0]==5) {
        /* Read-only: the last keyboard status the stock A5 35 handler (0x35220)
         * copied to RAM. The stock status bar draws battery (+14), charge mode
         * (+13) and connection (+16) from it; nothing here touches that path.
         */
        copy(f+1,(const void *)0x20004660,26); reply(f,27);
    } else { f[1]=0xee; reply(f,2); }
}

static void show_canvas(void);
static void display_region(void) {
    /* Decode beside the canvas, then copy whole words row by row into it: the
     * shown image never exposes a half-decoded rectangle, and LVGL redraws
     * only that rectangle (lv_obj_invalidate_area 0x295a4, int16 x1 y1 x2 y2).
     */
    copy(RX,RX+14,rt.jpeg_len);
    BARRIER();
    U8(0x20003662)=0;
    uint32_t result=FN(0x1cb88,uint32_t (*)(const void *,uint32_t,void *,uint32_t,uint32_t,uint32_t))
        (RX,rt.jpeg_len,0,REGION_RGB,REGION_BYTES,1);
    if (!result || !U8(0x20003662)) { rt.state=0; rt_event(2,ticks()-rt.frame_tick); return; }
    const uint32_t *src=(const uint32_t *)REGION_RGB;
    for (uint32_t r=0;r<rt.rh;r++) {
        uint32_t *dst=(uint32_t *)(rt.rgb+((rt.ry+r)*CANVAS_W+rt.rx)*2);
        for (uint32_t k=0;k<rt.rw/2;k++) dst[k]=*src++;
    }
    BARRIER();
    int16_t area[4]={(int16_t)rt.rx,(int16_t)rt.ry,(int16_t)(rt.rx+rt.rw-1),(int16_t)(rt.ry+rt.rh-1)};
    FN(0x295a4,void (*)(uint32_t,const int16_t *))(rt.image,area);
    rt.state=0; rt_event(1,ticks()-rt.frame_tick);
}
static void display(void) {
    uint32_t status=1;
    if (rt.state==2) {
        rt.frame_tick=ticks();
        status=rt_validate();
        if (status) { fail(status); return; }
        /* A frame drawn for another host page: the knob moved after the host started it,
         * before the host saw the page event. Shown, it would flash the previous page.
         */
        if (rt.region!=2 && rt.frame_tag && rt.frame_tag-1!=rt.page) { fail(7); return; }
        if (rt.region==1) { display_region(); return; }
        if (rt.region==2) {
            uint8_t *dst=slot(rt.cache_page);
            copy(dst,RX+8,rt.jpeg_len);
            rt.cache_len[rt.cache_page]=rt.jpeg_len;
            rt.cache_crc[rt.cache_page]=crc_update(0xffffffff,dst,rt.jpeg_len)^0xffffffff;
            rt.state=0; rt_event(1,ticks()-rt.frame_tick); return;
        }
        rt.canvas=0;   /* the canvas is rewritten from here on */
        /* GUI rendering cannot read the single output pool during mutation.
         * RGB copies span callbacks; the opaque black root remains visible.
         */
        FN(0x26798,void (*)(uint32_t,uint32_t))(rt.image,1);
        FN(0x23fa0,void (*)(uint32_t))(0);
        if (rt.type==0x5b) { rt.copy_offset=0; rt.state=3; return; }
        /* Match the aligned source used by all three stock JPEG paths. */
        copy(RX,RX+13,rt.jpeg_len);
        BARRIER();
        /* The stock wait is 10 RTOS ticks. Completion is checked separately. */
        U8(0x20003662)=0;
        uint32_t result=FN(0x1cb88,uint32_t (*)(const void *,uint32_t,void *,uint32_t,uint32_t,uint32_t))
            (RX,rt.jpeg_len,0,rt.rgb,RGB_BYTES,1);
        if (!result || !U8(0x20003662)) {
            rt.state=0; rt_event(2,ticks()-rt.frame_tick); return;
        }
    } else if (rt.state==3) {
        uint32_t n=rt.width*rt.height*2-rt.copy_offset;
        if (n>4096) n=4096;
        copy((uint8_t *)rt.rgb+rt.copy_offset,RX+9+rt.copy_offset,n);
        rt.copy_offset+=n;
        if (rt.copy_offset<rt.width*rt.height*2) return;
    } else return;
    show_canvas();
    rt.state=0; rt_event(1,ticks()-rt.frame_tick);
}
/* Shared by the frame and cache paths: the decoded rt.width x rt.height pixels go on screen. */
static void show_canvas(void) {
    BARRIER();
    rt.descriptor.header=4u | (rt.width<<10) | (rt.height<<21);
    rt.descriptor.size=rt.width*rt.height*2; rt.descriptor.data=rt.rgb;
    FN(0x24e78,void (*)(uint32_t,const Image *))(rt.image,&rt.descriptor);
    /* Packed decoder output needs no custom stride: center the frame object. */
    FN(0x2a770,void (*)(uint32_t,uint32_t,uint32_t))(rt.image,rt.width,rt.height);
    FN(0x2a6e8,void (*)(uint32_t,uint32_t,uint32_t))
        (rt.image,(CANVAS_W-rt.width)/2,(CANVAS_H-rt.height)/2);
    FN(0x26cb8,void (*)(uint32_t,uint32_t))(rt.image,1);
    FN(0x29558,void (*)(uint32_t))(rt.image);
    rt.canvas=rt.width==CANVAS_W && rt.height==CANVAS_H;
}
/* The knob reached host page rt.page (or entered the slot): show its cached JPEG at once. A stream
 * still arriving belongs to the previous page; it ends with frame-done 07 (superseded). The CRC
 * guards against stock code having reused the pool while the slot was hidden.
 */
static void show_cached(void) {
    uint32_t n=rt.cache_len[rt.page];
    if (!n || n>slot_bytes() || !rt.active) return;
    const uint8_t *src=slot(rt.page);
    if ((crc_update(0xffffffff,src,n)^0xffffffff)!=rt.cache_crc[rt.page]) { rt.cache_len[rt.page]=0; return; }
    if (rt.state) { rt.state=rt.total=rt.received=0; rt_event(7,0); }
    rt.frame_tick=ticks();
    rt.canvas=0;
    FN(0x26798,void (*)(uint32_t,uint32_t))(rt.image,1);
    FN(0x23fa0,void (*)(uint32_t))(0);
    copy(RX,src,n);   /* the aligned source of the stock JPEG paths */
    BARRIER();
    U8(0x20003662)=0;
    uint32_t result=FN(0x1cb88,uint32_t (*)(const void *,uint32_t,void *,uint32_t,uint32_t,uint32_t))
        (RX,n,0,rt.rgb,RGB_BYTES,1);
    if (!result || !U8(0x20003662)) return;   /* stays hidden: black until the host's frame */
    rt.width=CANVAS_W; rt.height=CANVAS_H;
    show_canvas();
    rt.decode_ms=ticks()-rt.frame_tick;
}

/* Called once per stock 10 ms GUI timer callback, including when mode is off. */
void rt_gui(void *timer) {
    if (rt.magic!=0x3154534e) return;
    /* lv_timer_t.period (offset 0): the GUI task loops every ~5 ms (osDelay 5 after each
     * lv_timer_handler, 0x3C5F8); run every loop while the slot is shown, stock 10 ms otherwise.
     */
    if (timer) U32(timer)=rt.active ? TICK_ACTIVE : TICK_STOCK;
    if (!rt.boot_seen) { rt.boot_seen=1; rt.boot_tick=ticks(); }
    rt.gui_ticks++;
    if (rt.overflow) {
        /* Overflow aborts the frame; never pass abandoned bytes to stock BA. Report
         * 04 only for a stream that was being received: an event with nothing in
         * progress reached the host as a stale reply to its next frame (hardware,
         * fast knob turns stalling the GUI task).
         */
        rt.tail=rt.head; rt.overflow=0;
        if (rt.state==1) fail(4);
    }
    uint32_t budget=16;
    while (budget-- && rt.tail!=rt.head && rt.tx_head-rt.tx_tail<TX_RING-2) {
        uint8_t *p=rt.reports[rt.tail & (RING-1)];
        BARRIER();
        /* Pipelining: the next frame's start may arrive before the previous one is shown.
         * Show it first (JPEG decodes complete in one call); an RGB copy still running
         * over ticks keeps the start queued for the next tick instead of refusing it.
         */
        if (p[0]==0xba && p[1]==6 && p[2]==0xaa && rt.state>=2) {
            if (rt.active && rt.state==2) display();
            if (rt.state>=2) break;
        }
        if (p[0]==0xba) rt_receive(p);
        else if (p[1]==0x3b) rt_control(p);
        /* Release a slot only after all reads from it have completed. */
        BARRIER(); rt.tail++;
    }
    if (rt.armed && ticks()-rt.host_tick>=HOST_TIMEOUT) {
        uint32_t visible=rt.active;
        if (visible) leave();
        else { rt.armed=0; rt.takeover=rt.pass=0; if (rt.state) fail(5); }
        if (visible) visibility_event(3);
    }
    if (rt.active && (rt.state==2 || rt.state==3)) display();
    if (rt.tx_head!=rt.tx_tail) {
        FN(0x32034,void (*)(const void *))(rt.tx[rt.tx_tail & (TX_RING-1)]);
        rt.tx_tail++;
    }
#ifdef RELEASE
    /* 60 s of uptime is the stability test; 1000 callbacks only show the GUI task runs.
     * (6000 was not reached within 120 s on hardware: on stock animated pages the GUI
     * loop runs the 10 ms timer far less often than every 10 ms.)
     */
    if (!rt.confirmed && rt.gui_ticks>=1000 && ticks()-rt.boot_tick>=60000) {
        rt.confirmed=1;
        /* Only the original routine may erase the trial flag page. */
        FN(0x2e010,uint32_t (*)(void))();
    }
#endif
    if (!rt.active) {
        stock_poll(timer);
        rt.restoring=0;
    } else FN(0x33fa4,void (*)(uint32_t))(0);
}

void rt_static(void *p) { if (!rt.active) stock_static(p); }
uint32_t rt_anim(uint32_t p) { return rt.active ? 0xffffffff : stock_anim(p); }
uint32_t rt_resource(const void *p) { return rt.active ? 2 : stock_resource(p); }
void rt_page(uint32_t p) { if (!rt.active) stock_page(p); }
