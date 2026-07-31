/*
 * 이미지 넣기 — After Effects 단독 실행 스크립트
 *
 * 설치도 설정도 명령 프롬프트도 필요 없습니다.
 * AE 에서 프로젝트를 열어둔 뒤
 *   File > Scripts > Run Script File...
 * 로 이 파일을 실행하세요.
 *
 * 하는 일:
 *   · 메인 컴프에 놓인 곡 컴프들을 타임라인 순서대로 찾는다
 *   · 고른 폴더의 이미지를 파일명 번호 순서대로 하나씩 넣는다
 *   · (선택) 곡 제목 텍스트도 파일명으로 바꾼다
 *
 * 적용 전에 무엇이 어디로 들어가는지 표로 보여주고, 적용 후에는
 * Ctrl+Z 한 번으로 전부 되돌릴 수 있습니다.
 *
 * ExtendScript 는 ES3 수준이라 let/const/forEach/JSON 을 쓸 수 없습니다.
 */

(function () {
    var IMAGE_EXT = /\.(png|jpg|jpeg|tif|tiff|psd|webp)$/i;

    // ── 기본 헬퍼 ───────────────────────────────────────────
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

    function pad2(n) {
        return (n < 10 ? "0" : "") + n;
    }

    function baseName(file) {
        var name = file.displayName || file.name;
        return decodeURI(name).replace(IMAGE_EXT, "");
    }

    /* 파일명 앞 번호로 정렬한다. "10-foo" 가 "2-bar" 보다 뒤에 오도록
       숫자로 비교한다. 번호가 없으면 이름순으로 뒤에 붙인다. */
    function leadingNumber(name) {
        var m = /^\s*(\d{1,3})(?!\d)/.exec(name);
        return m ? parseInt(m[1], 10) : null;
    }

    function sortImages(files) {
        files.sort(function (a, b) {
            var na = leadingNumber(baseName(a));
            var nb = leadingNumber(baseName(b));
            if (na !== null && nb !== null) return na - nb;
            if (na !== null) return -1;
            if (nb !== null) return 1;
            var sa = baseName(a).toLowerCase();
            var sb = baseName(b).toLowerCase();
            return sa < sb ? -1 : (sa > sb ? 1 : 0);
        });
        return files;
    }

    // ── 프로젝트 훑기 ───────────────────────────────────────
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

    /* 메인 컴프에 놓인 프리컴프들을 시작 시각 순으로. 각 항목에
       길이·꺼짐 여부·제목 텍스트 유무를 같이 담아 곡인지 판단에 쓴다. */
    function slotsOf(main) {
        var rows = [];
        for (var i = 1; i <= main.layers.length; i++) {
            var layer = main.layers[i];
            if (!(layer.source instanceof CompItem)) continue;
            var comp = layer.source;
            var hasText = false, imageLayer = null;
            for (var j = 1; j <= comp.layers.length; j++) {
                var kind = layerKind(comp.layers[j]);
                if (kind === "text") hasText = true;
                if (!imageLayer && (kind === "still" || kind === "footage")) {
                    imageLayer = comp.layers[j];
                }
            }
            rows.push({
                comp: comp,
                layer: layer,
                start: layer.startTime,
                span: layer.outPoint - layer.inPoint,
                enabled: layer.enabled,
                hasText: hasText,
                hasImage: !!imageLayer
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

    /* 곡일 가능성이 낮은 항목을 표시한다. 인트로는 훨씬 짧게 놓여 있고,
       안 쓰는 잔재는 꺼져 있으며, 곡 컴프에는 보통 제목 텍스트가 있다. */
    function markSongs(rows) {
        var spans = [], i;
        for (i = 0; i < rows.length; i++) {
            if (rows[i].span > 0) spans.push(rows[i].span);
        }
        var middle = median(spans);
        var withText = 0;
        for (i = 0; i < rows.length; i++) if (rows[i].hasText) withText++;

        for (i = 0; i < rows.length; i++) {
            var row = rows[i];
            var why = [];
            if (spans.length >= 3 && middle > 0 && row.span < middle * 0.4) {
                why.push("길이 " + Math.round(row.span) + "초");
            }
            if (!row.enabled) why.push("꺼져 있음");
            if (withText >= rows.length * 0.6 && !row.hasText) why.push("제목 없음");
            if (!row.hasImage) why.push("이미지 레이어 없음");
            row.isSong = why.length === 0;
            row.why = why.join(", ");
        }
        return rows;
    }

    // ── 시작 ────────────────────────────────────────────────
    if (!app.project || app.project.numItems === 0) {
        alert("먼저 After Effects 에서 프로젝트를 열어 주세요.");
        return;
    }
    var comps = allComps();
    if (!comps.length) {
        alert("이 프로젝트에는 컴포지션이 없습니다.");
        return;
    }

    var mainGuess = guessMain(comps);
    var state = {
        main: mainGuess,
        rows: markSongs(slotsOf(mainGuess)),
        images: []
    };

    // ── 창 만들기 ───────────────────────────────────────────
    var win = new Window("dialog", "이미지 넣기");
    win.orientation = "column";
    win.alignChildren = ["fill", "top"];
    win.preferredSize = [720, 560];
    win.margins = 16;

    // 메인 컴프 고르기
    var topRow = win.add("group");
    topRow.add("statictext", undefined, "메인 컴프:");
    var compNames = [];
    for (var c = 0; c < comps.length; c++) compNames.push(comps[c].name);
    var compPick = topRow.add("dropdownlist", undefined, compNames);
    compPick.selection = 0;
    for (var k = 0; k < comps.length; k++) {
        if (comps[k] === mainGuess) compPick.selection = k;
    }
    compPick.preferredSize.width = 260;

    // 이미지 폴더 고르기
    var folderRow = win.add("group");
    folderRow.alignChildren = ["fill", "center"];
    folderRow.add("statictext", undefined, "이미지 폴더:");
    var folderText = folderRow.add("edittext", undefined, "");
    folderText.preferredSize.width = 420;
    folderText.enabled = false;
    var browse = folderRow.add("button", undefined, "찾아보기…");

    win.add("statictext", undefined,
        "아래에서 곡 컴프만 선택하세요. 선택된 것에 이미지가 순서대로 들어갑니다.");

    var list = win.add("listbox", undefined, [], {
        multiselect: true,
        numberOfColumns: 5,
        showHeaders: true,
        columnTitles: ["#", "컴프", "길이", "들어갈 이미지", "비고"],
        columnWidths: [30, 220, 60, 220, 140]
    });
    list.preferredSize.height = 300;

    var buttons = win.add("group");
    var selectAll = buttons.add("button", undefined, "전체 선택");
    var selectGuess = buttons.add("button", undefined, "추천대로");
    var selectNone = buttons.add("button", undefined, "선택 해제");
    var titleBox = buttons.add("checkbox", undefined, "곡 제목도 파일명으로 바꾸기");
    titleBox.value = false;

    var status = win.add("statictext", undefined, "");
    status.characters = 80;

    var actions = win.add("group");
    actions.alignment = ["fill", "bottom"];
    actions.alignChildren = ["right", "center"];
    var spacer = actions.add("statictext", undefined, "");
    spacer.alignment = ["fill", "center"];
    var applyBtn = actions.add("button", undefined, "적용", { name: "ok" });
    var closeBtn = actions.add("button", undefined, "닫기", { name: "cancel" });

    // ── 표 그리기 ───────────────────────────────────────────
    function selectedIndexes() {
        var out = [];
        var sel = list.selection;
        if (!sel) return out;
        if (!(sel instanceof Array)) sel = [sel];
        for (var i = 0; i < sel.length; i++) out.push(sel[i].index);
        out.sort(function (a, b) { return a - b; });
        return out;
    }

    function redraw(keepSelection) {
        var wanted = keepSelection || [];
        list.removeAll();
        for (var i = 0; i < state.rows.length; i++) {
            var row = state.rows[i];
            var item = list.add("item", String(i + 1));
            item.subItems[0].text = row.comp.name;
            item.subItems[1].text = Math.round(row.span) + "초";
            item.subItems[2].text = "";
            item.subItems[3].text = row.why;
        }
        var sel = [];
        for (var j = 0; j < wanted.length; j++) {
            if (wanted[j] < list.items.length) sel.push(list.items[wanted[j]]);
        }
        list.selection = sel;
        refreshMapping();
    }

    function refreshMapping() {
        var chosen = selectedIndexes();
        for (var i = 0; i < list.items.length; i++) {
            list.items[i].subItems[2].text = "";
        }
        for (var n = 0; n < chosen.length; n++) {
            var file = state.images[n];
            list.items[chosen[n]].subItems[2].text =
                file ? decodeURI(file.displayName || file.name) : "(이미지 부족)";
        }
        var msg = "선택한 컴프 " + chosen.length + "개 · 이미지 " + state.images.length + "장";
        if (state.images.length && chosen.length !== state.images.length) {
            msg += "   ← 개수가 다릅니다";
        }
        status.text = msg;
        applyBtn.enabled = chosen.length > 0 && state.images.length > 0;
    }

    function guessSelection() {
        var out = [];
        for (var i = 0; i < state.rows.length; i++) {
            if (state.rows[i].isSong) out.push(i);
        }
        return out;
    }

    // ── 이벤트 ──────────────────────────────────────────────
    compPick.onChange = function () {
        state.main = comps[compPick.selection.index];
        state.rows = markSongs(slotsOf(state.main));
        redraw(guessSelection());
    };

    browse.onClick = function () {
        var folder = Folder.selectDialog("이미지가 들어 있는 폴더를 고르세요");
        if (!folder) return;
        folderText.text = folder.fsName;
        var files = folder.getFiles(function (f) {
            return (f instanceof File) && IMAGE_EXT.test(f.name);
        });
        state.images = sortImages(files);
        refreshMapping();
    };

    list.onChange = refreshMapping;
    selectAll.onClick = function () {
        var all = [];
        for (var i = 0; i < state.rows.length; i++) all.push(i);
        redraw(all);
    };
    selectGuess.onClick = function () { redraw(guessSelection()); };
    selectNone.onClick = function () { redraw([]); };

    applyBtn.onClick = function () {
        var chosen = selectedIndexes();
        if (chosen.length > state.images.length) {
            if (!confirm("컴프 " + chosen.length + "개에 이미지가 "
                         + state.images.length + "장뿐입니다.\n"
                         + "앞에서부터 " + state.images.length
                         + "개만 바꿀까요?")) return;
        }

        app.beginUndoGroup("이미지 넣기");
        var done = 0, failed = [];
        try {
            for (var n = 0; n < chosen.length && n < state.images.length; n++) {
                var row = state.rows[chosen[n]];
                var file = state.images[n];

                var target = null, titleLayer = null;
                for (var j = 1; j <= row.comp.layers.length; j++) {
                    var kind = layerKind(row.comp.layers[j]);
                    if (!target && (kind === "still" || kind === "footage")) {
                        target = row.comp.layers[j];
                    }
                    if (!titleLayer && kind === "text") titleLayer = row.comp.layers[j];
                }
                if (!target) { failed.push(row.comp.name + " (이미지 레이어 없음)"); continue; }

                var opts = new ImportOptions(file);
                if (opts.canImportAs(ImportAsType.FOOTAGE)) {
                    opts.importAs = ImportAsType.FOOTAGE;
                }
                var footage = app.project.importFile(opts);
                target.replaceSource(footage, false);

                /* 스케일에 키프레임이나 익스프레션이 걸려 있으면(켄번즈 같은
                   모션) 건드리지 않는다. 아니면 컴프를 꽉 채우도록 맞춘다. */
                var scale = target.property("ADBE Transform Group")
                                  .property("ADBE Scale");
                if (scale.numKeys === 0 && !scale.expressionEnabled
                        && footage.width && footage.height) {
                    var factor = Math.max(row.comp.width / footage.width,
                                          row.comp.height / footage.height);
                    scale.setValue([factor * 100, factor * 100]);
                }

                if (titleBox.value && titleLayer) {
                    var name = baseName(file).replace(/^\s*\d{1,3}\s*[-_.]?\s*/, "");
                    var prop = titleLayer.property("ADBE Text Properties")
                                         .property("ADBE Text Document");
                    var doc = prop.value;
                    doc.text = name;
                    prop.setValue(doc);
                }
                done++;
            }
        } catch (e) {
            app.endUndoGroup();
            alert("중간에 오류가 났습니다:\n" + e.toString()
                  + "\n\nCtrl+Z 를 누르면 되돌릴 수 있습니다.");
            return;
        }
        app.endUndoGroup();

        var msg = done + "개 컴프의 이미지를 바꿨습니다.";
        if (failed.length) msg += "\n\n건너뛴 것:\n  " + failed.join("\n  ");
        msg += "\n\n마음에 안 들면 Ctrl+Z 로 되돌릴 수 있습니다.";
        alert(msg);
        win.close();
    };

    closeBtn.onClick = function () { win.close(); };

    redraw(guessSelection());
    win.center();
    win.show();
})();
