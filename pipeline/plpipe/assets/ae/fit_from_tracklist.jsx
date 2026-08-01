/*
 * 트랙리스트로 맞추기 — After Effects 단독 실행 스크립트
 *
 * AE 에서 프로젝트를 열어둔 뒤
 *   File > Scripts > Run Script File...
 * 로 실행하세요. 설치도 설정도 필요 없습니다.
 *
 * 하는 일:
 *   · 곡 제목이 적힌 메모장 파일을 읽는다
 *   · 시각이 적혀 있으면 그 시각대로 곡 컴프를 배치한다
 *   · 곡 제목 텍스트를 메모장의 제목으로 바꾼다
 *
 * 메모장 형식은 웬만한 건 다 알아봅니다:
 *   0:00 First thing        |  1. 0:00 First thing
 *   [3:14] South side       |  South side - 3:14
 *   1. First thing          |  0:00<탭>First thing
 * '#' 로 시작하는 줄과 빈 줄은 건너뜁니다.
 *
 * 배치는 프레임 경계에 맞춥니다. AE 는 레이어 시각을 프레임 단위로만
 * 잡기 때문에, 안 맞추면 곡마다 오차가 쌓여 뒤로 갈수록 그림이 밀립니다.
 *
 * 적용 전에 표로 보여주고, 적용 후 Ctrl+Z 로 전부 되돌릴 수 있습니다.
 *
 * ExtendScript 는 ES3 수준이라 let/const/forEach/JSON 을 쓸 수 없습니다.
 */

(function () {
    // ── 트랙리스트 파싱 ─────────────────────────────────────
    function parseTrackline(raw) {
        var line = String(raw).replace(/^﻿/, "").replace(/[\r\n]+$/, "");
        if (!/\S/.test(line)) return null;
        if (/^\s*[#;\/]/.test(line)) return null;          // 주석

        /* 줄 앞의 트랙 번호를 먼저 떼어낸다. "1. 0:00 제목" 처럼 번호가
           시각보다 앞에 오는 형식 때문이다. 구분자에서 콜론은 뺀다 —
           "0:00" 의 "0:" 을 번호로 오인하기 때문. */
        var numbered = /^\s*\d{1,3}\s*[.)\]\-–—]\s*/;
        line = line.replace(numbered, "");

        var time = null, leading = false, rest = line;

        var head = /^\s*[\[\(]?\s*(\d{1,2}):([0-5]?\d)(?::([0-5]?\d))?\s*[\]\)]?\s*[-–—.)\]]?\s*/;
        var m = head.exec(line);
        if (m) {
            time = m[3] !== undefined
                ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3])
                : (+m[1]) * 60 + (+m[2]);
            leading = true;
            rest = line.substring(m[0].length);
        } else {
            var tail = /\s*[\[\(]?\s*(\d{1,2}):([0-5]?\d)(?::([0-5]?\d))?\s*[\]\)]?\s*$/;
            var t = tail.exec(line);
            if (t) {
                time = t[3] !== undefined
                    ? (+t[1]) * 3600 + (+t[2]) * 60 + (+t[3])
                    : (+t[1]) * 60 + (+t[2]);
                rest = line.substring(0, t.index);
            }
        }

        rest = rest.replace(numbered, "");
        rest = rest.replace(/^[\s\-–—:.\t]+/, "").replace(/[\s\-–—:.\t]+$/, "");
        if (!rest && time === null) return null;
        return { time: time, leading: leading, title: rest };
    }

    /* 메모장이 UTF-8 일 수도 ANSI(한글 윈도우면 CP949) 일 수도 있다.
       UTF-8 로 읽어보고 깨진 문자가 보이면 다시 읽는다. */
    function readTextFile(file) {
        var encodings = ["UTF-8", "CP949", "EUC-KR", "ASCII"];
        for (var i = 0; i < encodings.length; i++) {
            try {
                file.encoding = encodings[i];
                if (!file.open("r")) continue;
                var text = file.read();
                file.close();
                if (text && text.indexOf("�") === -1) return text;
                if (i === encodings.length - 1) return text;
            } catch (e) {
                try { file.close(); } catch (e2) {}
            }
        }
        return "";
    }

    /* 실제 메모장에는 트랙리스트만 있지 않다. 훅 문장, "tracklist" 머리글,
       "total runtime: 52:44", 크레딧, 댓글 초안 같은 줄이 섞여 있다.
       그래서 줄 맨 앞에 시각이 있는 줄만 곡으로 본다. "total runtime: 52:44"
       는 시각이 줄 끝에 있으므로 걸러진다.

       시각이 앞에 붙은 줄이 하나도 없으면 그런 형식이 아니라는 뜻이므로,
       그때만 제목만 적힌 목록으로 보고 전부 받는다. */
    function parseTracklist(file) {
        var text = readTextFile(file);
        var lines = text.split(/\r\n|\r|\n/);
        var timed = [], loose = [];
        for (var i = 0; i < lines.length; i++) {
            var row = parseTrackline(lines[i]);
            if (!row) continue;
            if (row.leading) timed.push(row);
            loose.push(row);
        }
        return timed.length ? timed : loose;
    }

    // ── AE 헬퍼 ─────────────────────────────────────────────
    function layerKind(layer) {
        if (layer instanceof TextLayer) return "text";
        if (layer instanceof ShapeLayer) return "shape";
        if (layer.nullLayer) return "null";
        if (layer.source instanceof CompItem) return "precomp";
        if (layer.source instanceof FootageItem) {
            var src = layer.source;
            if (src.mainSource instanceof SolidSource) return "solid";
            if (src.hasAudio && !src.hasVideo) return "audio";
            if (src.hasVideo && src.duration === 0) return "still";
            return "footage";
        }
        return "other";
    }

    function clock(seconds) {
        if (seconds === null || seconds === undefined) return "";
        var total = Math.floor(seconds);
        var h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60);
        var s = total % 60;
        function two(n) { return (n < 10 ? "0" : "") + n; }
        return h ? (h + ":" + two(m) + ":" + two(s)) : (m + ":" + two(s));
    }

    function snapUp(seconds, fps) {
        if (!fps || fps <= 0) return seconds;
        return Math.ceil(seconds * fps - 1e-6) / fps;
    }

    function allComps() {
        var out = [];
        for (var i = 1; i <= app.project.numItems; i++) {
            if (app.project.item(i) instanceof CompItem) out.push(app.project.item(i));
        }
        return out;
    }

    function guessMain(comps) {
        var best = null, bestCount = -1;
        for (var i = 0; i < comps.length; i++) {
            var count = 0;
            for (var j = 1; j <= comps[i].layers.length; j++) {
                if (comps[i].layers[j].source instanceof CompItem) count++;
            }
            if (count > bestCount) { bestCount = count; best = comps[i]; }
        }
        return best;
    }

    function slotsOf(main) {
        var rows = [];
        for (var i = 1; i <= main.layers.length; i++) {
            var layer = main.layers[i];
            if (!(layer.source instanceof CompItem)) continue;
            var comp = layer.source, textLayer = null;
            for (var j = 1; j <= comp.layers.length; j++) {
                if (!textLayer && layerKind(comp.layers[j]) === "text") {
                    textLayer = comp.layers[j];
                }
            }
            rows.push({
                comp: comp, layer: layer, textLayer: textLayer,
                start: layer.startTime, span: layer.outPoint - layer.inPoint,
                enabled: layer.enabled, hasText: !!textLayer
            });
        }
        rows.sort(function (a, b) { return a.start - b.start; });
        return rows;
    }

    function median(values) {
        if (!values.length) return 0;
        var copy = values.slice();
        copy.sort(function (a, b) { return a - b; });
        var mid = Math.floor(copy.length / 2);
        return copy.length % 2 ? copy[mid] : (copy[mid - 1] + copy[mid]) / 2;
    }

    function markSongs(rows) {
        var spans = [], i;
        for (i = 0; i < rows.length; i++) if (rows[i].span > 0) spans.push(rows[i].span);
        var middle = median(spans), withText = 0;
        for (i = 0; i < rows.length; i++) if (rows[i].hasText) withText++;
        for (i = 0; i < rows.length; i++) {
            var row = rows[i], why = [];
            if (spans.length >= 3 && middle > 0 && row.span < middle * 0.4) {
                why.push("길이 " + Math.round(row.span) + "초");
            }
            if (!row.enabled) why.push("꺼져 있음");
            if (withText >= rows.length * 0.6 && !row.hasText) why.push("제목 없음");
            row.isSong = why.length === 0;
            row.why = why.join(", ");
        }
        return rows;
    }

    /* 곡이 아닌 컴프(인트로 등)를 AE 설정에 기억해 둔다. 프로젝트마다
       인트로 이름이 다르고 길이로는 늘 가려낼 수 없으므로, 한 번 지정하면
       다음 회차부터는 그대로 쓴다. */
    var SETTINGS_SECTION = "plpipe";
    var SETTINGS_KEY = "notSongs";

    function loadExcluded() {
        try {
            if (!app.settings.haveSetting(SETTINGS_SECTION, SETTINGS_KEY)) return [];
            var raw = app.settings.getSetting(SETTINGS_SECTION, SETTINGS_KEY);
            return raw ? raw.split("\n") : [];
        } catch (e) { return []; }
    }

    function saveExcluded(names) {
        try {
            app.settings.saveSetting(SETTINGS_SECTION, SETTINGS_KEY, names.join("\n"));
        } catch (e) {}
    }

    function nameIn(list, name) {
        for (var i = 0; i < list.length; i++) if (list[i] === name) return true;
        return false;
    }

    /* 마지막 곡이 어디서 끝나는지는 메인의 오디오 길이로 정한다. */
    function audioEnd(main) {
        for (var i = 1; i <= main.layers.length; i++) {
            if (layerKind(main.layers[i]) === "audio") {
                return { name: main.layers[i].source.name,
                         end: main.layers[i].source.duration };
            }
        }
        return null;
    }

    // ── 시작 ────────────────────────────────────────────────
    if (!app.project || app.project.numItems === 0) {
        alert("먼저 After Effects 에서 프로젝트를 열어 주세요.");
        return;
    }
    var comps = allComps();
    if (!comps.length) { alert("이 프로젝트에는 컴포지션이 없습니다."); return; }

    var mainGuess = guessMain(comps);
    var state = { main: mainGuess, rows: markSongs(slotsOf(mainGuess)), tracks: [] };

    // ── 창 ──────────────────────────────────────────────────
    var VERSION = "v4";   // 창 제목에 표시된다. 파일을 바꿨는지 바로 확인용.
    var win = new Window("dialog", "트랙리스트로 맞추기  " + VERSION);
    win.orientation = "column";
    win.alignChildren = ["fill", "top"];
    win.preferredSize = [780, 590];
    win.margins = 16;

    var topRow = win.add("group");
    topRow.add("statictext", undefined, "메인 컴프:");
    var names = [];
    for (var c = 0; c < comps.length; c++) names.push(comps[c].name);
    var compPick = topRow.add("dropdownlist", undefined, names);
    compPick.selection = 0;
    for (var k = 0; k < comps.length; k++) if (comps[k] === mainGuess) compPick.selection = k;
    compPick.preferredSize.width = 260;

    var fileRow = win.add("group");
    fileRow.alignChildren = ["fill", "center"];
    fileRow.add("statictext", undefined, "트랙리스트 파일:");
    var fileText = fileRow.add("edittext", undefined, "");
    fileText.preferredSize.width = 430;
    fileText.enabled = false;
    var browse = fileRow.add("button", undefined, "찾아보기…");

    win.add("statictext", undefined,
        "곡 컴프만 선택하세요. 위에서부터 트랙리스트 순서대로 짝지어집니다.");

    var list = win.add("listbox", undefined, [], {
        multiselect: true, showHeaders: true,
        columnTitles: ["#", "컴프", "지금 길이", "새 제목", "새 시작", "새 길이", "비고"],
        columnWidths: [28, 165, 66, 190, 66, 66, 110],
        numberOfColumns: 7
    });
    list.preferredSize.height = 300;

    var buttons = win.add("group");
    var selectGuess = buttons.add("button", undefined, "추천대로");
    var selectAll = buttons.add("button", undefined, "전체 선택");
    var selectNone = buttons.add("button", undefined, "선택 해제");
    var timeBox = buttons.add("checkbox", undefined, "시각대로 배치");
    var titleBoxUI = buttons.add("checkbox", undefined, "제목 바꾸기");
    timeBox.value = true;
    titleBoxUI.value = true;

    var status = win.add("statictext", undefined, "", { multiline: true });
    status.preferredSize.height = 36;

    var actions = win.add("group");
    actions.alignment = ["fill", "bottom"];
    actions.alignChildren = ["right", "center"];
    var spacer = actions.add("statictext", undefined, "");
    spacer.alignment = ["fill", "center"];
    var applyBtn = actions.add("button", undefined, "적용", { name: "ok" });
    var closeBtn = actions.add("button", undefined, "닫기", { name: "cancel" });

    // ── 계산 ────────────────────────────────────────────────
    function selectedIndexes() {
        var out = [], sel = list.selection;
        if (!sel) return out;
        if (!(sel instanceof Array)) sel = [sel];
        for (var i = 0; i < sel.length; i++) out.push(sel[i].index);
        out.sort(function (a, b) { return a - b; });
        return out;
    }

    function hasTimes() {
        for (var i = 0; i < state.tracks.length; i++) {
            if (state.tracks[i].time !== null) return true;
        }
        return false;
    }

    function plan() {
        var chosen = selectedIndexes();
        var fps = state.main.frameRate;
        var tail = audioEnd(state.main);
        var out = [];
        var count = Math.min(chosen.length, state.tracks.length);
        for (var n = 0; n < count; n++) {
            var track = state.tracks[n];
            var start = track.time === null ? null : snapUp(track.time, fps);
            out.push({ index: chosen[n], row: state.rows[chosen[n]],
                       title: track.title, start: start, end: null });
        }
        // 각 곡의 끝은 다음 곡의 시작. 마지막은 오디오 끝까지.
        for (var i = 0; i < out.length; i++) {
            if (out[i].start === null) continue;
            var next = null;
            for (var j = i + 1; j < out.length; j++) {
                if (out[j].start !== null) { next = out[j].start; break; }
            }
            if (next === null && tail) next = snapUp(tail.end, fps);
            out[i].end = next;
        }
        return { items: out, tail: tail };
    }

    function redraw(keep) {
        var wanted = keep || [];
        list.removeAll();
        for (var i = 0; i < state.rows.length; i++) {
            var item = list.add("item", String(i + 1));
            item.subItems[0].text = state.rows[i].comp.name;
            item.subItems[1].text = clock(state.rows[i].span);
            item.subItems[2].text = "";
            item.subItems[3].text = "";
            item.subItems[4].text = "";
            item.subItems[5].text = state.rows[i].why;
        }
        var sel = [];
        for (var j = 0; j < wanted.length; j++) {
            if (wanted[j] < list.items.length) sel.push(list.items[wanted[j]]);
        }
        list.selection = sel;
        refresh();
    }

    function refresh() {
        for (var i = 0; i < list.items.length; i++) {
            list.items[i].subItems[2].text = "";
            list.items[i].subItems[3].text = "";
            list.items[i].subItems[4].text = "";
        }
        var laid = plan();
        for (var n = 0; n < laid.items.length; n++) {
            var it = laid.items[n], row = list.items[it.index];
            row.subItems[2].text = it.title;
            row.subItems[3].text = clock(it.start);
            if (it.start !== null && it.end !== null) {
                row.subItems[4].text = clock(it.end - it.start);
            }
        }

        var chosen = selectedIndexes();
        var lines = "선택한 컴프 " + chosen.length + "개 / 전체 "
                    + state.rows.length + "개 · 트랙리스트 "
                    + state.tracks.length + "곡";
        if (state.usedMemory) lines += "   (지난번 제외 설정을 기억했습니다)";
        if (state.tracks.length && chosen.length !== state.tracks.length) {
            lines += "   ← 개수가 다릅니다";
        }
        if (state.tracks.length && !hasTimes()) {
            lines += "\n트랙리스트에 시각이 없어 배치는 못 합니다. 제목만 바꿉니다.";
            timeBox.value = false;
            timeBox.enabled = false;
        } else if (state.tracks.length) {
            timeBox.enabled = true;
            if (laid.tail) {
                lines += "\n마지막 곡은 " + laid.tail.name + " 끝("
                         + clock(laid.tail.end) + ")까지 이어집니다.";
            } else {
                lines += "\n메인에 오디오 레이어가 없어 마지막 곡의 끝을 모릅니다.";
            }
        }
        status.text = lines;
        applyBtn.enabled = laid.items.length > 0
            && (timeBox.value || titleBoxUI.value);
    }

    /* 구조로 추측한 결과가 트랙리스트 곡 수와 안 맞으면, 곡 수를 더 믿는다.
       지난 회차 배치가 남아 있으면 길이가 제각각이라 추측이 잘 빗나가는데,
       메모장에 몇 곡인지는 분명히 적혀 있기 때문이다. */
    function guessSelection() {
        var i;

        /* 1순위: 지난번에 "곡 아님" 으로 지정해 둔 컴프. 이름으로 기억하므로
           길이가 어떻든 정확히 그것만 빠진다. */
        var remembered = loadExcluded(), matched = 0;
        for (i = 0; i < state.rows.length; i++) {
            if (nameIn(remembered, state.rows[i].comp.name)) matched++;
        }
        if (matched) {
            var kept0 = [];
            for (i = 0; i < state.rows.length; i++) {
                if (!nameIn(remembered, state.rows[i].comp.name)) kept0.push(i);
            }
            state.usedMemory = true;
            if (!state.tracks.length || kept0.length === state.tracks.length) {
                return kept0;
            }
        }
        state.usedMemory = false;

        var guessed = [];
        for (i = 0; i < state.rows.length; i++) {
            if (state.rows[i].isSong) guessed.push(i);
        }
        var want = state.tracks.length;
        if (!want || guessed.length === want) return guessed;

        var total = state.rows.length, all = [], i2;
        for (i2 = 0; i2 < total; i2++) all.push(i2);
        if (total <= want) return all;           // 모자라면 전부 (개수 경고가 뜬다)

        /* 남는 개수만큼 짧은 것부터 뺀다. 인트로·아웃트로·잔재는 곡보다
           짧게 놓여 있기 때문이다. 꺼진 레이어는 무조건 먼저 뺀다. */
        var order = [];
        for (i2 = 0; i2 < total; i2++) order.push(i2);
        order.sort(function (a, b) {
            var ea = state.rows[a].enabled ? 1 : 0;
            var eb = state.rows[b].enabled ? 1 : 0;
            if (ea !== eb) return ea - eb;       // 꺼진 것 먼저
            return state.rows[a].span - state.rows[b].span;
        });
        var dropped = {};
        for (i2 = 0; i2 < total - want; i2++) dropped[order[i2]] = true;
        var kept = [];
        for (i2 = 0; i2 < total; i2++) if (!dropped[i2]) kept.push(i2);
        return kept;
    }

    // ── 이벤트 ──────────────────────────────────────────────
    compPick.onChange = function () {
        state.main = comps[compPick.selection.index];
        state.rows = markSongs(slotsOf(state.main));
        redraw(guessSelection());
    };

    browse.onClick = function () {
        var file = File.openDialog("곡 제목이 적힌 메모장 파일을 고르세요",
                                   "텍스트:*.txt;*.csv;*.md,모든 파일:*.*");
        if (!file) return;
        fileText.text = file.fsName;
        state.tracks = parseTracklist(file);
        if (!state.tracks.length) {
            alert("이 파일에서 곡을 하나도 읽지 못했습니다.\n"
                  + "한 줄에 한 곡씩 적혀 있는지 확인해 주세요.");
        }
        // 곡 수를 알게 됐으니 선택을 다시 잡는다.
        redraw(guessSelection());
    };

    list.onChange = refresh;
    timeBox.onClick = refresh;
    titleBoxUI.onClick = refresh;
    selectGuess.onClick = function () { redraw(guessSelection()); };
    selectAll.onClick = function () {
        var all = [];
        for (var i = 0; i < state.rows.length; i++) all.push(i);
        redraw(all);
    };
    selectNone.onClick = function () { redraw([]); };

    applyBtn.onClick = function () {
        var laid = plan();
        if (!laid.items.length) return;
        var chosen = selectedIndexes();
        if (laid.items.length < chosen.length) {
            if (!confirm("트랙리스트가 모자라 앞에서부터 " + laid.items.length
                         + "곡만 처리합니다.\n계속할까요?")) return;
        }

        /* 이번에 곡이 아니라고 본 컴프를 기억해 둔다. 다음 회차에는
           길이로 추측하지 않고 이 목록을 그대로 쓴다. */
        var excludedNames = [];
        for (var x = 0; x < state.rows.length; x++) {
            var inSel = false;
            for (var y = 0; y < chosen.length; y++) if (chosen[y] === x) inSel = true;
            if (!inSel) excludedNames.push(state.rows[x].comp.name);
        }
        saveExcluded(excludedNames);

        app.beginUndoGroup("트랙리스트로 맞추기");
        var moved = 0, renamed = 0, skipped = [];
        try {
            for (var n = 0; n < laid.items.length; n++) {
                var it = laid.items[n];

                if (timeBox.value && it.start !== null && it.end !== null
                        && it.end > it.start) {
                    it.row.layer.startTime = it.start;
                    it.row.layer.inPoint = it.start;
                    it.row.layer.outPoint = it.end;
                    moved++;
                }

                if (titleBoxUI.value && it.title) {
                    if (it.row.textLayer) {
                        var prop = it.row.textLayer
                            .property("ADBE Text Properties")
                            .property("ADBE Text Document");
                        var doc = prop.value;
                        doc.text = it.title;
                        prop.setValue(doc);
                        renamed++;
                    } else {
                        skipped.push(it.row.comp.name + " (텍스트 레이어 없음)");
                    }
                }
            }
        } catch (e) {
            app.endUndoGroup();
            alert("중간에 오류가 났습니다:\n" + e.toString()
                  + "\n\nCtrl+Z 로 되돌릴 수 있습니다.");
            return;
        }
        app.endUndoGroup();

        var msg = "";
        if (moved) msg += moved + "개 컴프를 배치했습니다.\n";
        if (renamed) msg += renamed + "개 제목을 바꿨습니다.\n";
        if (!msg) msg = "바뀐 것이 없습니다.\n";
        if (skipped.length) msg += "\n건너뛴 것:\n  " + skipped.join("\n  ") + "\n";
        msg += "\n마음에 안 들면 Ctrl+Z 로 되돌릴 수 있습니다.";
        alert(msg);
        win.close();
    };

    closeBtn.onClick = function () { win.close(); };

    redraw(guessSelection());
    win.center();
    win.show();
})();
