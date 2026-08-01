/*
 * fit_from_tracklist.jsx 를 AE 없이 돌려보기 위한 하네스.
 * ScriptUI 와 파일 읽기까지 흉내내어 실제 배치 결과를 확인한다.
 */
import fs from "node:fs";

function AVLayer() {} function TextLayer() {} function ShapeLayer() {}
function CompItem() {} function FootageItem() {} function SolidSource() {}
Object.assign(globalThis, { AVLayer, TextLayer, ShapeLayer, CompItem,
                            FootageItem, SolidSource });

const FPS = 30000 / 1001;

function prop(value) {
  return { numKeys: 0, expressionEnabled: false, _v: value,
           get value() { return this._v; }, setValue(v) { this._v = v; } };
}

/* 글자 폭 모형. AE 는 실제 폰트로 재지만, 여기서는 "글자 수 × 크기" 로
   근사한다. 재고-줄이고-다시 재는 흐름을 보는 게 목적이라 비례하면 된다. */
const CHAR_W = 0.55, LINE_H = 1.2;

function mkLayer({ name, type = AVLayer, source = null, text = null,
                   enabled = true, start = 0, span = 190,
                   fontSize = 180, comment = "",
                   pos = [1920, 1080], anchor = [1920, 1080] }) {
  const l = Object.create(type.prototype);
  const doc = { text, fontSize };
  const scale = prop([100, 100]), position = prop(pos);
  const anchorProp = prop(anchor), rotation = prop(0);
  Object.assign(l, {
    name, source, enabled, nullLayer: false, parent: null, comment,
    startTime: start, inPoint: start, outPoint: start + span, _doc: doc,
    property(id) {
      if (id === "ADBE Transform Group") return { property(pid) {
        if (pid === "ADBE Position") return position;
        if (pid === "ADBE Anchor Point") return anchorProp;
        if (pid === "ADBE Rotate Z") return rotation;
        return scale;
      } };
      if (id === "ADBE Text Properties") return { property: () => ({
        get value() { return { text: doc.text, fontSize: doc.fontSize }; },
        setValue(v) { doc.text = v.text; doc.fontSize = v.fontSize; } }) };
      return null;
    },
    /* 텍스트는 가운데 정렬이라 상자가 앵커를 중심으로 좌우로 자란다. */
    sourceRectAtTime() {
      if (type === TextLayer) {
        const w = String(doc.text || "").length * doc.fontSize * CHAR_W;
        return { left: -w / 2, top: -doc.fontSize * LINE_H,
                 width: w, height: doc.fontSize * LINE_H };
      }
      return { left: 0, top: 0,
               width: source ? source.width : 0,
               height: source ? source.height : 0 };
    },
  });
  return l;
}
function mkComp({ name, layers = [], duration = 3600 }) {
  const c = Object.create(CompItem.prototype);
  // 나중에 레이어를 밀어 넣어도 1-기반 접근이 따라오도록 접근자로 만든다.
  const coll = { get length() { return layers.length; } };
  for (let i = 0; i < 100; i++) {
    Object.defineProperty(coll, i + 1, {
      get() { return layers[i]; }, configurable: true });
  }
  Object.assign(c, { name, width: 3840, height: 2160, frameRate: FPS,
                     duration, layers: coll, _layers: layers });
  return c;
}
function mkFootage({ name, audio = false, duration = 0,
                    width = 2944, height = 1648 }) {
  const f = Object.create(FootageItem.prototype);
  Object.assign(f, { name, width, height, duration,
                     hasVideo: !audio, hasAudio: audio, mainSource: {} });
  return f;
}

/* argv[8] = "preshrunk" 면 지난 회차에 줄여 둔 제목으로 시작한다. 코멘트에
   원래 크기가 적혀 있으므로, 짧은 제목이 오면 그 크기로 돌아가야 한다. */
const preshrunk = process.argv[8] === "preshrunk";

// 지난 회차 배치가 그대로 남아 있는 상태 (곡 길이가 지금과 다름)
const TOTAL = 3164.0;   // 52:44
const songs = [], mainLayers = [];
let t = 0;
const intro = mkComp({ name: "Intro",
  layers: [mkLayer({ name: "intro.png", source: mkFootage({ name: "intro.png" }) })] });
/* argv[7] = "long" 이면 인트로를 곡보다 길게 만든다. 길이로는 못 가려내는
   상황이라, 이름을 기억하는 기능이 필요한 경우다. */
const introSpan = process.argv[7] === "long" ? 500 : 11;
mainLayers.push(mkLayer({ name: "Intro", source: intro, start: 0, span: introSpan }));
t = introSpan;
// 지난 회차 배치라 곡 길이가 제각각이다 (추측이 빗나가기 쉬운 상태)
const STALE = [402, 118, 355, 96, 289, 141, 388, 102, 331, 155, 377, 88, 344];
for (let i = 1; i <= 13; i++) {
  const comp = mkComp({ name: i === 1 ? "Change - Things" : `Change - Things ${i}`,
    layers: [ mkLayer(Object.assign({ name: `제목${i}`, type: TextLayer,
                        text: `지난회차 ${i}`, pos: [1920, 1900], anchor: [0, 0] },
                        preshrunk ? { fontSize: 40, comment: "plpipe-base-size:180" } : {})),
              mkLayer({ name: `img${i}.png`, source: mkFootage({ name: `img${i}.png` }) }) ] });
  songs.push(comp);
  mainLayers.push(mkLayer({ name: comp.name, source: comp, start: t, span: STALE[i - 1] }));
  t += STALE[i - 1];
}
const main = mkComp({ name: "Main", duration: TOTAL, layers: [
  mkLayer({ name: "LOGO.png", pos: [200, 200], anchor: [150, 150],
            source: mkFootage({ name: "LOGO.png", width: 300, height: 300 }) }),
  mkLayer({ name: "ep08_full.wav", source: mkFootage({ name: "ep08_full.wav",
                                                       audio: true, duration: TOTAL }) }),
  ...mainLayers ] });
/* 곡이 아닌 잔재 컴프를 붙일 수 있다.
   argv[4] = 개수, argv[5] = "off" 면 꺼진 레이어로. */
const extraCount = parseInt(process.argv[4] || "0", 10) || 0;
const extraOff = process.argv[5] === "off";
for (let z = 0; z < extraCount; z++) {
  const c = mkComp({ name: "잔재 " + (z + 1), layers: [
    mkLayer({ name: "t", type: TextLayer, text: "이전 텍스트" }),
    mkLayer({ name: "i.png", source: mkFootage({ name: "i.png" }) }) ] });
  songs.push(c);
  main._layers.push(mkLayer({ name: c.name, source: c,
    start: 3200 + z * 30, span: extraOff ? 400 : 25, enabled: !extraOff }));
}

const items = [main, intro, ...songs];

// AE 설정 저장소 목업. argv[6] 으로 "지난번에 기억해 둔" 값을 넣을 수 있다.
const store = {};
if (process.argv[6]) store["plpipe/notSongs"] = process.argv[6];
globalThis.app = {
  project: { numItems: items.length, item: (i) => items[i - 1] },
  beginUndoGroup() {}, endUndoGroup() {},
  settings: {
    haveSetting: (sec, key) => (sec + "/" + key) in store,
    getSetting: (sec, key) => store[sec + "/" + key] || "",
    saveSetting: (sec, key, val) => { store[sec + "/" + key] = val; },
  },
};
globalThis.__store = store;

// ── UI + 파일 목업 ─────────────────────────────────────────
const reg = { buttons: {}, checkbox: {}, listbox: null, statictext: [] };
function control(type, text) {
  const c = { type, text: text ?? "", subItems: [], items: [],
    preferredSize: {}, alignChildren: [], alignment: [], margins: 0,
    enabled: true, value: false, selection: null,
    add(kind, ...rest) {
      const content = kind === "item" ? rest[0] : rest[1];
      if (kind === "item") {
        const it = control("item", content);
        it.index = this.items.length;
        for (let i = 0; i < 6; i++) it.subItems.push(control("sub", ""));
        this.items.push(it);
        return it;
      }
      const child = control(kind, content);
      if (kind === "button") reg.buttons[content] = child;
      if (kind === "checkbox") reg.checkbox[content] = child;
      if (kind === "listbox") reg.listbox = child;
      if (kind === "statictext") reg.statictext.push(child);
      if (kind === "dropdownlist") {
        child.items = (content || []).map((t, i) => ({ text: t, index: i }));
        child.selection = child.items[0];
      }
      return child;
    },
    removeAll() { this.items = []; }, show() {}, close() {}, center() {},
  };
  return c;
}
globalThis.Window = function () { return control("window"); };

const TRACKLIST = process.argv[3];
globalThis.File = function (p) {
  this.fsName = p; this.name = p; this.encoding = "UTF-8";
  let buf = null, pos = 0;
  this.open = () => { buf = fs.readFileSync(p, "utf8"); pos = 0; return true; };
  this.read = () => buf;
  this.close = () => true;
};
globalThis.File.openDialog = () => new globalThis.File(TRACKLIST);
globalThis.alert = (m) => { (globalThis.__alerts ||= []).push(m); };
globalThis.confirm = () => true;
globalThis.decodeURI = (s) => s;

const src = fs.readFileSync(process.argv[2], "utf8");
(0, eval)(src);

reg.buttons["찾아보기…"].onClick();
const lb = reg.listbox;
const before = lb.items.map((it) => ({
  comp: it.subItems[0].text, title: it.subItems[1].text,
  start: it.subItems[2].text, span: it.subItems[3].text, why: it.subItems[4].text }));
reg.buttons["적용"].onClick();

process.stdout.write(JSON.stringify({
  preview: before,
  status: reg.statictext[reg.statictext.length - 2].text,
  applied: main._layers.filter((l) => l.source instanceof CompItem).map((l) => ({
    comp: l.source.name, start: l.startTime, out: l.outPoint,
    title: l.source._layers.filter((x) => x._doc.text !== null)
             .map((x) => x._doc.text)[0] ?? null })),
  alerts: globalThis.__alerts, fps: FPS,
  saved: globalThis.__store["plpipe/notSongs"],
  // 제목 레이어의 최종 상태 — 자동 줄이기 결과.
  titles: [intro, ...songs].reduce((acc, comp) => {
    comp._layers.filter((l) => l instanceof TextLayer).forEach((l) => {
      acc.push({ comp: comp.name, text: l._doc.text,
                 fontSize: l._doc.fontSize, comment: l.comment });
    });
    return acc;
  }, []),
}, null, 1));
