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

function mkLayer({ name, type = AVLayer, source = null, text = null,
                   enabled = true, start = 0, span = 190 }) {
  const l = Object.create(type.prototype);
  const doc = { text };
  Object.assign(l, {
    name, source, enabled, nullLayer: false,
    startTime: start, inPoint: start, outPoint: start + span, _doc: doc,
    property(id) {
      if (id === "ADBE Text Properties") return { property: () => ({
        get value() { return { text: doc.text }; },
        setValue(v) { doc.text = v.text; } }) };
      return null;
    },
  });
  return l;
}
function mkComp({ name, layers = [], duration = 3600 }) {
  const c = Object.create(CompItem.prototype);
  const coll = { get length() { return layers.length; } };
  layers.forEach((l, i) => { coll[i + 1] = l; });
  Object.assign(c, { name, width: 3840, height: 2160, frameRate: FPS,
                     duration, layers: coll, _layers: layers });
  return c;
}
function mkFootage({ name, audio = false, duration = 0 }) {
  const f = Object.create(FootageItem.prototype);
  Object.assign(f, { name, width: 2944, height: 1648, duration,
                     hasVideo: !audio, hasAudio: audio, mainSource: {} });
  return f;
}

// 지난 회차 배치가 그대로 남아 있는 상태 (곡 길이가 지금과 다름)
const TOTAL = 3164.0;   // 52:44
const songs = [], mainLayers = [];
let t = 0;
const intro = mkComp({ name: "Intro",
  layers: [mkLayer({ name: "intro.png", source: mkFootage({ name: "intro.png" }) })] });
mainLayers.push(mkLayer({ name: "Intro", source: intro, start: 0, span: 11 }));
t = 11;
// 지난 회차 배치라 곡 길이가 제각각이다 (추측이 빗나가기 쉬운 상태)
const STALE = [402, 118, 355, 96, 289, 141, 388, 102, 331, 155, 377, 88, 344];
for (let i = 1; i <= 13; i++) {
  const comp = mkComp({ name: i === 1 ? "Change - Things" : `Change - Things ${i}`,
    layers: [ mkLayer({ name: `제목${i}`, type: TextLayer, text: `지난회차 ${i}` }),
              mkLayer({ name: `img${i}.png`, source: mkFootage({ name: `img${i}.png` }) }) ] });
  songs.push(comp);
  mainLayers.push(mkLayer({ name: comp.name, source: comp, start: t, span: STALE[i - 1] }));
  t += STALE[i - 1];
}
const main = mkComp({ name: "Main", duration: TOTAL, layers: [
  mkLayer({ name: "LOGO.png", source: mkFootage({ name: "LOGO.png" }) }),
  mkLayer({ name: "ep08_full.wav", source: mkFootage({ name: "ep08_full.wav",
                                                       audio: true, duration: TOTAL }) }),
  ...mainLayers ] });
const items = [main, intro, ...songs];

globalThis.app = { project: { numItems: items.length, item: (i) => items[i - 1] },
                   beginUndoGroup() {}, endUndoGroup() {} };

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
}, null, 1));
