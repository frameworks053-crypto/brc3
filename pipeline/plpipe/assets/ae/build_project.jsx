/*
 * plpipe — AE 프로젝트 빌드
 *
 * 템플릿 .aep 를 열어 곡 수만큼 슬롯 컴프를 복제하고, 이미지 교체 + 텍스트
 * 채우기 + 길이 조정을 한 뒤 렌더 큐에 담아 새 .aep 로 저장한다.
 * 실제 렌더는 aerender CLI 가 맡는다.
 *
 * mode = "segments" : 곡별 짧은 클립만 큐에 담는다 (권장, 훨씬 빠름)
 * mode = "full"     : 전체 길이 메인 컴프 하나를 큐에 담는다
 *
 * PLPIPE_JOB 은 파이썬이 이 파일 앞에 붙여준다.
 * ExtendScript 는 ES3 수준이라 let/const/forEach/JSON 을 쓸 수 없다.
 */

(function () {
    var job = PLPIPE_JOB;
    var names = job.names;
    var log = [];

    function say(msg) {
        log.push(msg);
        $.writeln("plpipe: " + msg);
    }

    function fail(msg) {
        throw new Error("plpipe: " + msg);
    }

    // ── 조회 헬퍼 ───────────────────────────────────────────
    function findItem(name, wantComp) {
        var items = app.project.items;
        for (var i = 1; i <= items.length; i++) {
            var item = items[i];
            if (item.name !== name) continue;
            if (wantComp && !(item instanceof CompItem)) continue;
            return item;
        }
        return null;
    }

    function findLayer(comp, name) {
        if (!name) return null;
        for (var i = 1; i <= comp.layers.length; i++) {
            if (comp.layers[i].name === name) return comp.layers[i];
        }
        return null;
    }

    function layerKind(layer) {
        if (layer instanceof TextLayer) return "text";
        if (layer instanceof ShapeLayer) return "shape";
        if (layer instanceof CameraLayer) return "camera";
        if (layer instanceof LightLayer) return "light";
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

    function matchesKind(layer, kind) {
        var actual = layerKind(layer);
        // @image 는 정지 이미지와 동영상 소스를 모두 받는다.
        if (kind === "image") return actual === "still" || actual === "footage";
        return actual === kind;
    }

    /* 레이어 선택자.
         "IMAGE"        이름이 정확히 일치하는 레이어
         "@still"       첫 번째 스틸 이미지 레이어
         "@text:last"   마지막 텍스트 레이어
         "@image:2"     두 번째 이미지 레이어
         "#3"           3번 레이어 (스택 순서)
       템플릿 레이어 이름이 곡 제목이나 파일명이라 고정 이름을 쓸 수 없을 때
       종류로 찾기 위한 장치다. */
    function resolveLayer(comp, selector, matches) {
        if (!selector) return null;
        var first = selector.charAt(0);

        if (first === "#") {
            var idx = parseInt(selector.substring(1), 10);
            if (isNaN(idx) || idx < 1 || idx > comp.layers.length) return null;
            return comp.layers[idx];
        }

        if (first === "@") {
            var parts = selector.substring(1).split(":");
            var kind = parts[0];
            var pick = parts.length > 1 ? parts[1] : "first";
            var hits = [];
            for (var i = 1; i <= comp.layers.length; i++) {
                if (matchesKind(comp.layers[i], kind)) hits.push(comp.layers[i]);
            }
            if (matches) {
                matches.count = hits.length;
                matches.kind = kind;
            }
            if (!hits.length) return null;
            if (pick === "last") return hits[hits.length - 1];
            if (pick === "first") return hits[0];
            var n = parseInt(pick, 10);
            return (!isNaN(n) && n >= 1 && n <= hits.length) ? hits[n - 1] : null;
        }

        return findLayer(comp, selector);
    }

    function compNameList() {
        var out = [];
        var items = app.project.items;
        for (var i = 1; i <= items.length; i++) {
            if (items[i] instanceof CompItem) out.push(items[i].name);
        }
        return out.join(", ");
    }

    function importFile(path) {
        var file = new File(path);
        if (!file.exists) fail("파일이 없습니다: " + path);
        var opts = new ImportOptions(file);
        if (opts.canImportAs(ImportAsType.FOOTAGE)) {
            opts.importAs = ImportAsType.FOOTAGE;
        }
        return app.project.importFile(opts);
    }

    // ── 편집 헬퍼 ───────────────────────────────────────────
    function setText(comp, selector, value) {
        var layer = resolveLayer(comp, selector);
        if (!layer || !(layer instanceof TextLayer)) return false;
        var prop = layer.property("ADBE Text Properties")
                        .property("ADBE Text Document");
        var doc = prop.value;
        doc.text = String(value);
        prop.setValue(doc);
        return true;
    }

    /* 이미지가 컴프를 꽉 채우도록 스케일을 맞춘다.
       단, 스케일에 키프레임이나 익스프레션이 걸려 있으면(켄번즈 같은 모션)
       템플릿 의도를 깨뜨리므로 건드리지 않는다. */
    function fitCover(layer, comp) {
        var scale = layer.property("ADBE Transform Group").property("ADBE Scale");
        if (scale.numKeys > 0 || scale.expressionEnabled) return false;
        var src = layer.source;
        if (!src || !src.width || !src.height) return false;
        var factor = Math.max(comp.width / src.width, comp.height / src.height);
        scale.setValue([factor * 100, factor * 100]);
        return true;
    }

    function queue(comp, outputPath) {
        var rqItem = app.project.renderQueue.items.add(comp);
        try {
            rqItem.applyTemplate(job.render.settings);
        } catch (e) {
            fail("렌더 설정 템플릿 '" + job.render.settings + "' 을 적용하지 못했습니다. "
                 + "사용 가능: " + rqItem.templates.join(", "));
        }
        var om = rqItem.outputModule(1);
        try {
            om.applyTemplate(job.render.module);
        } catch (e2) {
            fail("출력 모듈 템플릿 '" + job.render.module + "' 을 적용하지 못했습니다. "
                 + "사용 가능: " + om.templates.join(", "));
        }
        var file = new File(outputPath);
        file.parent.create();
        om.file = file;
        return rqItem;
    }

    // ── 본 작업 ─────────────────────────────────────────────
    app.open(new File(job.project));
    app.beginUndoGroup("plpipe build");

    // 이전 실행이 남긴 렌더 큐 항목을 비운다.
    var rq = app.project.renderQueue;
    while (rq.numItems > 0) rq.item(1).remove();

    /* 슬롯 컴프를 얻는 두 가지 방식.
         (a) 템플릿에 슬롯 컴프가 하나 있고 그걸 곡 수만큼 복제한다.
         (b) 곡별 컴프가 이미 프로젝트에 있고, 그 안의 내용만 바꾼다.
       (b) 는 곡마다 컴프를 손으로 만들어 둔 템플릿을 위한 것이다. */
    var usingExisting = job.slots.length > 0 && !!job.slots[0].existing;

    var slotTemplate = null;
    if (!usingExisting) {
        slotTemplate = findItem(names.slot_comp, true);
        if (!slotTemplate) {
            fail("슬롯 컴프 '" + names.slot_comp + "' 를 찾지 못했습니다. "
                 + "프로젝트의 컴프: " + compNameList());
        }
        // 같은 이름으로 이전에 만든 슬롯이 있으면 지우고 새로 만든다.
        for (var d = 0; d < job.slots.length; d++) {
            var stale = findItem(job.slots[d].name, true);
            if (stale) stale.remove();
        }
    }
    var staleMain = findItem(job.main.name, true);
    if (staleMain) staleMain.remove();

    var built = [];
    for (var s = 0; s < job.slots.length; s++) {
        var slot = job.slots[s];
        var comp;

        if (usingExisting) {
            comp = findItem(slot.existing, true);
            if (!comp) {
                fail("곡 " + slot.index + " 의 컴프 '" + slot.existing
                     + "' 를 찾지 못했습니다. 프로젝트의 컴프: " + compNameList());
            }
            // 사용자가 만든 컴프이므로 크기·프레임레이트·길이는 건드리지 않는다.
        } else {
            comp = slotTemplate.duplicate();
            comp.name = slot.name;
            comp.duration = slot.duration;
            if (job.video.width) comp.width = job.video.width;
            if (job.video.height) comp.height = job.video.height;
            if (job.video.fps) comp.frameRate = job.video.fps;
        }

        var found = {};
        var imageLayer = resolveLayer(comp, names.image_layer, found);
        if (!imageLayer) {
            fail("컴프 '" + comp.name + "' 안에서 이미지 레이어 '"
                 + names.image_layer + "' 를 찾지 못했습니다.");
        }
        if (found.count > 1) {
            say("경고: '" + comp.name + "' 에 " + found.kind + " 레이어가 "
                + found.count + "개 있습니다. '" + imageLayer.name + "' 을 바꿉니다.");
        }
        imageLayer.replaceSource(importFile(slot.image), false);
        fitCover(imageLayer, comp);

        if (!usingExisting) {
            // 새로 만든 컴프만 레이어를 전체 길이에 맞춰 늘린다.
            for (var L = 1; L <= comp.layers.length; L++) {
                var layer = comp.layers[L];
                if (layer.outPoint < comp.duration) layer.outPoint = comp.duration;
            }
        }

        setText(comp, names.title_layer, slot.title);
        setText(comp, names.index_layer, slot.label);

        built.push({ comp: comp, slot: slot });
        say("슬롯 " + comp.name + " ← " + slot.title);
    }

    if (job.mode === "segments") {
        for (var q = 0; q < built.length; q++) {
            queue(built[q].comp, built[q].slot.output);
        }
        say("렌더 큐: 곡별 클립 " + built.length + "개");
    } else {
        // 이번에 준비한 슬롯 컴프들. 메인에서 이것들의 기존 인스턴스를 걷어낸다.
        function isSlotInstance(layer) {
            if (!(layer.source instanceof CompItem)) return false;
            if (slotTemplate && layer.source === slotTemplate) return true;
            for (var b = 0; b < built.length; b++) {
                if (layer.source === built[b].comp) return true;
            }
            return false;
        }

        var mainTemplate = findItem(names.main_comp, true);
        var main;
        var anchorBelow = null;    // 원래 슬롯들 바로 아래에 있던 레이어
        var slotsAtBottom = false; // 원래 슬롯들이 스택 맨 아래였는지
        if (mainTemplate) {
            main = mainTemplate.duplicate();
            main.name = job.main.name;

            /* 템플릿 메인 컴프의 기존 슬롯 인스턴스를 치운다. 새로 넣을
               슬롯들을 원래 있던 깊이에 되돌려 놓아야 하므로, 지우기 전에
               가장 아래 슬롯 바로 밑의 레이어를 기억해 둔다. AE 의
               layers.add() 는 새 레이어를 맨 위에 넣기 때문에, 이걸 안 하면
               배경 위에 얹어둔 로고나 오버레이가 이미지에 가려진다. */
            var lowestSlot = -1;
            for (var f = 1; f <= main.layers.length; f++) {
                if (isSlotInstance(main.layers[f])) lowestSlot = f;
            }
            if (lowestSlot > 0) {
                if (lowestSlot < main.layers.length) {
                    anchorBelow = main.layers[lowestSlot + 1];
                } else {
                    /* 슬롯이 스택 맨 아래에 있었다는 뜻. 로고·텍스트·오디오
                       스펙트럼 같은 레이어가 그 위에 얹혀 있는 구성이므로,
                       새 슬롯도 맨 아래로 보내야 한다. 안 그러면 이미지가
                       그것들을 다 덮어버린다. */
                    slotsAtBottom = true;
                }
            }
            for (var m = main.layers.length; m >= 1; m--) {
                if (isSlotInstance(main.layers[m])) main.layers[m].remove();
            }
        } else {
            main = app.project.items.addComp(
                job.main.name, job.video.width, job.video.height,
                1, job.main.duration, job.video.fps
            );
        }
        main.duration = job.main.duration;

        var byName = {};
        for (var b2 = 0; b2 < built.length; b2++) {
            byName[built[b2].comp.name] = built[b2].comp;
            byName[built[b2].slot.name] = built[b2].comp;
        }

        for (var p = 0; p < job.main.placements.length; p++) {
            var place = job.main.placements[p];
            var source = byName[place.slot];
            if (!source) fail("배치할 슬롯을 찾지 못했습니다: " + place.slot);
            var inst = main.layers.add(source);
            inst.startTime = place.start;
            inst.inPoint = place.start;
            inst.outPoint = Math.min(place.end, job.main.duration);
            // 원래 슬롯이 있던 깊이로 되돌린다. AE 의 layers.add() 는 새
            // 레이어를 맨 위에 넣으므로, 그대로 두면 위에 얹힌 로고·텍스트·
            // 오디오 스펙트럼이 전부 이미지에 가려진다.
            if (anchorBelow) inst.moveBefore(anchorBelow);
            else if (slotsAtBottom) inst.moveToEnd();
        }

        if (job.main.audio) {
            var audioLayer = resolveLayer(main, names.audio_layer);
            var footage = importFile(job.main.audio);
            if (audioLayer) {
                audioLayer.replaceSource(footage, false);
            } else {
                audioLayer = main.layers.add(footage);
                audioLayer.name = names.audio_layer || "AUDIO";
            }
            audioLayer.startTime = 0;
            audioLayer.inPoint = 0;
            audioLayer.outPoint = job.main.duration;
        }

        queue(main, job.main.output);
        say("렌더 큐: 메인 컴프 1개 (" + job.main.duration + "s)");
    }

    app.endUndoGroup();

    var target = new File(job.save_as);
    target.parent.create();
    app.project.save(target);
    say("저장 완료 → " + job.save_as);

    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
})();
