# plpipe — 유튜브 플레이리스트 채널 자동화

수노 음악생성 → 미드저니 이미지생성 → AE 편집 → 렌더 까지를 명령어 하나로 잇는 파이프라인.

13곡을 만들고 각 곡에 이미지 한 장씩 붙인 뒤 전체를 한 번 더 반복해서
1시간이 조금 넘는 롱폼 영상 하나를 뽑는 구성을 기준으로 만들었습니다.
업로드는 자동으로 하지 않고, 붙여넣기만 하면 되는 메타데이터 파일까지 만들어 줍니다.

의존성은 **파이썬 3.11 이상과 ffmpeg 뿐**입니다. 외부 파이썬 패키지를 설치하지 않습니다.

```
곡 13개 ─┐
         ├→ ingest → audio(정규화·타임라인·마스터) → ae build → aerender → assemble → out/*.mp4
이미지 13개 ┘                                                                        out/metadata.txt
```

---

## 1. 설치

```bash
# ffmpeg 먼저
winget install Gyan.FFmpeg      # Windows
brew install ffmpeg             # macOS

# plpipe
cd pipeline
pip install -e .

# 작업 폴더 만들기 (원하는 위치에서)
mkdir ~/playlist && cd ~/playlist
plpipe init .
```

`plpipe init` 이 `config.toml`, `projects/`, `templates/` 를 만듭니다.
AE 템플릿 `.aep` 를 `templates/` 에 넣으세요.

> `pip install` 없이 쓰려면 `PYTHONPATH=/경로/pipeline python3 -m plpipe ...` 로도 됩니다.

---

## 2. AE 템플릿 맞추기 (제일 먼저 할 일)

파이프라인은 템플릿을 새로 만들지 않습니다. **가지고 계신 .aep 를 그대로 쓰고**,
그 안의 컴프·레이어 이름만 알면 됩니다.

템플릿에 필요한 건 딱 하나, **곡 하나에 해당하는 프리컴프**입니다.
그 안에 이미지가 들어갈 레이어가 있으면 되고, 제목·번호 텍스트 레이어는 있으면 채워주고 없으면 넘어갑니다.

### 구조 확인하기

**설치 전이라면** — `plpipe/assets/ae/dump_template.jsx` 를 AE 에서 바로 실행하세요.
파이썬도 설정도 필요 없습니다.

1. AE 에서 템플릿 프로젝트를 엽니다
2. `File > Scripts > Run Script File…` → `dump_template.jsx` 선택
3. 창에 뜬 리포트를 복사 (`.aep` 옆에 `plpipe-template-report.txt` 로도 저장됩니다)

프로젝트를 읽기만 하고 아무것도 바꾸지 않습니다.

**설치 후라면** 같은 걸 명령줄에서:

```bash
plpipe ae inspect
```

```
컴프 3개

  SLOT  1920x1080 @24fps  180.0s
       1. TITLE  (text)
       2. INDEX  (text)
       3. IMAGE  (still/video) [키프레임된 스케일]
       4. VIGNETTE  (solid)
  MAIN  1920x1080 @24fps  600.0s
       ...

렌더 설정 템플릿: Best Settings, Draft Settings, DV Settings
출력 모듈 템플릿: Lossless, AIFF 48kHz, Alpha Only, H.264 - Match Render Settings
```

여기서 본 이름을 `config.toml` 에 적어줍니다:

```toml
[ae]
slot_comp   = "SLOT"
image_layer = "IMAGE"
title_layer = "TITLE"     # 없으면 ""
index_layer = "INDEX"     # 없으면 ""
render_settings = "Best Settings"
output_module   = "Lossless"
```

### 두 가지 템플릿 형태

**(a) 슬롯 컴프 하나를 복제** — 곡 하나에 해당하는 프리컴프가 하나 있고, 파이프라인이 곡 수만큼 복제합니다.

```toml
[ae]
slot_comp   = "SLOT"
image_layer = "IMAGE"
title_layer = "TITLE"
```

**(b) 곡별 컴프가 이미 있음** — 곡마다 컴프를 손으로 만들어 둔 경우. 복제하지 않고 **기존 컴프의 내용만 바꿉니다.**

```toml
[ae]
slot_comps = ["Change - Things 21", "Change - Things", "Change - Things 2"]
```

재생 순서대로 적으면 됩니다. 개수가 트랙 수와 다르면 오류로 잡아줍니다.

### 레이어 선택자

레이어 이름이 곡 제목이나 이미지 파일명이라 고정돼 있지 않으면, 이름 대신 **종류로** 찾으세요.

| 선택자 | 뜻 |
|---|---|
| `"IMAGE"` | 이름이 정확히 일치하는 레이어 |
| `"@still"` | 첫 번째 정지 이미지 레이어 |
| `"@text"` | 첫 번째 텍스트 레이어 |
| `"@audio"` | 첫 번째 오디오 레이어 |
| `"@text:last"` | 마지막 텍스트 레이어 |
| `"@still:2"` | 두 번째 스틸 레이어 |
| `"#3"` | 3번 레이어 (1이 맨 위) |

종류는 `still` · `text` · `audio` · `footage` · `image`(스틸+영상) · `solid` · `shape` · `precomp` 를 씁니다.
컴프에 해당 종류가 여러 개면 어떤 걸 바꿨는지 로그로 알려줍니다.

빌드할 때 파이프라인이 하는 일:

- `SLOT` 컴프를 곡 수만큼 복제 → `PL_SLOT_01` … `PL_SLOT_13`
- 각 복제본의 `IMAGE` 레이어 소스를 해당 곡의 이미지로 교체
- 컴프를 꽉 채우도록 스케일 조정 — **단 스케일에 키프레임이나 익스프레션이 걸려 있으면 건드리지 않습니다.**
  켄번즈 줌 같은 모션이 있으면 템플릿 의도가 그대로 유지됩니다. 그때는 이미지 비율을 컴프에 맞춰 뽑아두세요.
- `TITLE` / `INDEX` 텍스트를 곡 제목·번호로 교체
- 컴프 길이를 곡 길이에 맞추고, 모든 레이어가 끝까지 덮도록 늘림
- 렌더 큐에 담아 `work/ae/<slug>.aep` 로 저장

원본 .aep 는 건드리지 않습니다.

`full` 모드에서는 메인 컴프도 다시 조립합니다. 이때 **템플릿 메인 컴프의 배경·로고·오버레이
레이어는 그대로 살아남고**, 새로 넣는 슬롯들은 원래 슬롯이 있던 레이어 순서로 되돌려집니다.
(AE 의 `layers.add()` 는 새 레이어를 맨 위에 넣기 때문에, 이걸 안 하면 로고가 이미지에 가려집니다.)

`lead_in` / `lead_out` 구간에는 슬롯이 없으므로 메인 컴프의 배경 레이어가 보입니다.
배경이 없으면 그 구간은 검게 나오니, `full` 모드를 쓸 거면 템플릿에 배경을 깔아두거나
`lead_in`/`lead_out` 을 0 으로 두세요. (`segments` 모드는 첫/마지막 프레임을 붙잡아 두므로
이 문제가 없습니다.)

---

## 3. 한 편 만들기

```bash
# 1) 프로젝트 생성 (제목 템플릿 변수도 같이 넘길 수 있음)
plpipe new "2026-08 midnight lofi" --var mood="Midnight Lofi" --var activity=study

# 2) 곡과 이미지를 drop 폴더에 넣기 — 파일명 앞에 번호를 붙입니다
#    projects/2026-08-midnight-lofi/drop/audio/01-Neon Rain.mp3
#    projects/2026-08-midnight-lofi/drop/images/01-Neon Rain.png

# 3) 나머지 전부
plpipe run
```

`plpipe run` = `ingest` → `audio` → `ae build` → `render` → `meta`.

결과물은 `projects/<slug>/out/` 에 생깁니다:

| 파일 | 내용 |
|---|---|
| `<slug>.mp4` | 업로드할 최종 영상 |
| `master.wav` | -14 LUFS 로 맞춘 마스터 오디오 |
| `metadata.txt` | 제목·설명·챕터 타임스탬프·태그 (복붙용) |
| `metadata.json` | 나중에 업로드까지 자동화할 때 쓸 형태 |

단계별로 끊어서 돌려도 됩니다. 모든 상태가 `manifest.json` 에 남기 때문에
어디서 멈추든 이어서 진행됩니다.

```bash
plpipe status          # 어디까지 됐는지
plpipe audio --force   # 정규화만 다시
plpipe render --skip-ae  # AE 렌더는 그대로 두고 조립만 다시
```

### 곡 제목과 프롬프트

`ingest` 는 파일명에서 제목을 뽑습니다 (`01-Neon Rain.mp3` → `Neon Rain`).
프롬프트를 미리 정해두고 싶으면 목록 파일로 시작하세요:

```
# tracks.txt  —  제목 | 프롬프트
Neon Rain      | lofi hip hop, rainy tokyo night, warm rhodes, vinyl crackle, 80bpm
Late Transfer  | lofi, jazzy guitar, subway ambience, mellow, 75bpm
```

```bash
plpipe new "midnight lofi" --titles tracks.txt
plpipe prompts    # 수동 생성용으로 프롬프트 목록 출력
```

`manifest.json` 을 직접 열어서 `title` / `prompt` / `image_prompt` 를 고쳐도 됩니다.

---

## 4. 렌더 방식 — `segments` 를 쓰세요

`[ae] mode` 로 고릅니다.

**`segments` (기본, 권장)** — AE 는 곡별 짧은 클립 13개만 렌더하고, 이어붙이기는 ffmpeg 이 합니다.
2회차 반복 구간은 화면이 1회차와 똑같으므로 **같은 클립 파일을 다시 참조**합니다.

**`full`** — 1시간짜리 메인 컴프를 AE 가 통째로 렌더합니다.

13곡 × 2회 = 약 1시간 10분 영상 기준:

| | AE 가 실제로 렌더하는 분량 | 비고 |
|---|---|---|
| `segments` | 약 35분 (13클립) | 반복분은 파일 재사용 |
| `full` | 약 70분 | 같은 화면을 두 번 렌더 |

`ffmpeg` 모드도 있습니다. AE 없이 정지 이미지만으로 만드는 폴백이라,
템플릿을 다 세팅하기 전에 파이프라인이 제대로 도는지 확인할 때 쓰면 좋습니다.

### 프레임 정렬에 대해 (29.97fps 포함)

AE 는 컴프 길이를 **정수 프레임 단위로만** 잡습니다. 곡 길이는 3분 22.437초처럼
프레임에 안 맞는 값이라, 그대로 두면 곡마다 최대 반 프레임씩 오차가 나고
26구간을 지나면 영상이 오디오보다 **1초 가까이 밀립니다.**

그래서 곡 사이 간격을 프레임 경계에 맞게 최대 한 프레임(24fps 기준 41ms)까지 늘려서,
'한 곡의 시작에서 다음 곡의 시작까지'가 항상 정수 프레임이 되도록 합니다.
설정에 `gap = 1.5` 를 넣으면 실제로는 곡마다 1.500~1.541초 사이가 되는데, 귀로는 구분되지 않습니다.
덕분에 이미지 전환이 곡 시작과 **프레임 단위까지 정확히** 맞습니다.

29.97 / 23.976 / 59.94 같은 NTSC 표기는 실제로 `30000/1001` 처럼 나누어떨어지지 않는 값입니다.
설정에 `fps = 29.97` 이라고 적어도 내부에서는 정확한 분수로 계산합니다.

---

## 5. 오디오 처리

### 오디오를 직접 합쳐서 넣는 경우

`[audio] source = "manual"` 로 두면 파이프라인이 오디오를 만들지 않고,
**이미 합쳐둔 파일에서 곡 경계를 자동으로 찾습니다.**

```toml
[audio]
source = "manual"
master = "drop/master.wav"
```

```bash
plpipe cue
```

```
곡 경계 검출: master.wav  (임계 -45.0dB, 최소 무음 0.4s)

  #          시작         구간  제목
  1     0:00:00      23.6s  Neon Rain
  2     0:00:24      28.2s  Late Transfer
  ...
```

곡 사이 무음을 찾아 그 **한가운데**를 경계로 잡습니다. 이미지가 다음 곡보다
살짝 먼저 바뀌는데, 이게 자연스럽습니다. 경계는 프레임에 맞춰집니다.

경계가 틀리게 잡히면:

```bash
plpipe cue --threshold -30      # 무음 판정을 느슨하게
plpipe cue --min-silence 0.8    # 곡 안의 짧은 정적을 무시
```

그래도 안 맞으면 `manifest.json` 의 `timeline.segments` 에서 `start` 를 직접 고치면 됩니다.
곡별 원본 파일도 `drop/audio` 에 넣어두면 검출 결과를 그 길이와 대조해서 이상한 곳을 알려줍니다.

### 파이프라인이 합치는 경우 (`source = "pipeline"`)

- 곡마다 2패스 loudnorm 으로 **-14 LUFS / -1.0 dBTP** 로 맞춥니다 (유튜브 기준).
  Suno 는 곡마다 음량 편차가 큰데, 이걸 안 맞추면 플레이리스트 중간에 볼륨이 튑니다.
- 앞뒤 무음을 자동으로 잘라냅니다 (`trim_silence_db`, 기본 -50dB). 무음 구간이 챕터를 밀지 않게.
- 무음 구간은 정확한 길이의 무음 파일을 끼워 넣는 방식이라 샘플 단위로 정확합니다.
- `crossfade` 를 0보다 크게 주면 간격 대신 크로스페이드가 걸립니다.
  다만 **오디오만** 겹칩니다 — 화면 전환은 컷입니다. 영상 크로스페이드가 필요하면 AE 템플릿에서 처리하세요.

## 6. 메타데이터

`plpipe meta` 가 유튜브 챕터 규칙을 검사해서 안 맞으면 경고합니다:
첫 챕터 0:00, 최소 3개, 각 구간 10초 이상, 제목 100자·설명 5000자 한도.

반복 구간에는 `(repeat)` 을 붙입니다. `chapters_all_passes = false` 로 두면 1회차만 넣습니다.

---

## 7. 자동화 어디까지 되나 — Suno / Midjourney 현실

**요약: 이 볼륨(영상당 13곡 + 13장)에서는 비용이 의미 없는 수준입니다. 진짜 변수는 API 유무입니다.**

### 음악

| 방식 | 영상 1편(13곡) 비용 | 자동화 |
|---|---|---|
| Suno 공식 구독 Pro ($10/월, 약 500곡) | **약 $0.26** | ✗ 수동 |
| 서드파티 래퍼 API | $0.18 ~ $1.44 | ✓ |

Suno 는 **공개 API 가 없습니다.** 코드로 부르려면 서드파티 래퍼를 써야 하고,
곡당 $0.014(APIPASS) ~ $0.111(EvoLink) 정도입니다. 계정 정지 리스크는 감수해야 합니다.

공식 구독이 곡당 $0.02 수준으로 압도적으로 쌉니다. 그리고 어차피 **쓸 만한 곡을 고르는 건 사람 일**입니다.
Suno 는 같은 프롬프트로도 편차가 커서, 13곡 뽑으려면 30~40곡은 만들어놓고 골라야 합니다.
자동 생성해봐야 그 선별 과정이 없어지지 않으므로, **음악은 수동을 권합니다.**

### 이미지

| 모델 | 장당 | 영상 1편(13장) | 공식 API | 강점 |
|---|---|---|---|---|
| Midjourney (Basic $10/월, 200장) | ~$0.05 | **약 $0.65** | ✗ | **예술성·스타일 1위** |
| FLUX 2 Pro | $0.055 | $0.72 | ✓ | 포토리얼리즘 1위 |
| FLUX (1MP 기본) | ~$0.03 | $0.39 | ✓ | 가성비 |
| fal / Replicate 호스팅 오픈모델 | $0.008~0.04 | $0.10~0.52 | ✓ | 최저가 |
| GPT Image 1 Mini | $0.005 | $0.065 | ✓ | 최저가, 프롬프트 충실도 |
| GPT Image 2 High | $0.211 | $2.74 | ✓ | 텍스트 렌더링 |
| Imagen 4 | ~$0.04 | ~$0.52 | ✓ | 인물 얼굴·사실감 |

퀄리티는 용도에 따라 갈립니다. **2026년 기준 Midjourney V7/V8 이 여전히 스타일라이즈드 아트에서 앞섭니다** —
lofi 플레이리스트 커버처럼 "분위기"가 전부인 이미지에는 이게 제일 잘 맞습니다.
FLUX 2 는 포토리얼리즘, Imagen 4 는 인물 얼굴이 강점이라 결이 다릅니다.

Midjourney 도 **공식 API 가 없습니다.** 자동화하려면 Discord 봇을 흉내내는 서드파티 프록시를 써야 하고,
이건 Midjourney ToS 위반이라 계정이 날아갈 수 있습니다. 월 $10 짜리 계정을 걸 만한 가치가 있는지 생각해보세요.
**13장이면 수동으로 10분입니다.**

### 그래서 추천

```toml
[music]
provider = "manual"     # Suno 에서 직접 뽑아 고르기

[image]
provider = "manual"     # Midjourney 감성이 필요하면
# provider = "bfl"      # 완전 자동화가 필요하면 FLUX
```

수동 모드에서도 파이프라인의 **나머지 전부(정규화·타임라인·AE 빌드·렌더·메타데이터)가 자동**입니다.
여기가 원래 시간을 제일 많이 잡아먹는 구간이고, 실제로 자동화 가치가 큰 부분입니다.

자동 생성으로 바꾸고 싶으면 프로바이더만 갈아끼우면 됩니다:

```bash
export BFL_API_KEY=...        # 키는 설정 파일이 아니라 환경변수로
plpipe images
```

지원: `openai` / `bfl`(FLUX) / `fal` — 이미지, `suno_api` — 음악.

---

## 8. 명령어

| 명령 | 하는 일 |
|---|---|
| `plpipe init [폴더]` | `config.toml` 과 작업 폴더 생성 |
| `plpipe new <이름>` | 새 영상 프로젝트 생성 |
| `plpipe status` | 트랙별 진행 상황 |
| `plpipe prompts` | 수동 생성용 프롬프트 목록 |
| `plpipe music` | 설정된 프로바이더로 곡 생성 |
| `plpipe images` | 설정된 프로바이더로 이미지 생성 |
| `plpipe ingest` | drop 폴더 파일을 트랙에 배정 |
| `plpipe audio` | 정규화 → 타임라인 → 마스터 오디오 |
| `plpipe cue` | 합쳐둔 마스터에서 곡 경계 검출 |
| `plpipe ae inspect` | 템플릿 컴프·레이어 구조 덤프 |
| `plpipe ae build` | 렌더용 .aep 생성 |
| `plpipe render` | aerender 실행 후 최종 mp4 조립 |
| `plpipe meta` | 제목·설명·챕터·태그 생성 |
| `plpipe run` | ingest부터 meta까지 전부 |

프로젝트를 지정하지 않으면 **가장 최근 프로젝트**를 씁니다. `-p <slug>` 로 지정할 수 있습니다.

## 9. 프로젝트 폴더 구조

```
projects/<slug>/
  manifest.json        모든 상태 (트랙·길이·타임라인·진행단계)
  drop/audio/          여기에 곡을 넣습니다
  drop/images/         여기에 이미지를 넣습니다
  work/
    norm/              정규화된 wav
    segments/          AE 가 렌더한 곡별 클립
    ae/                생성된 .jsx, .aep, 로그
  out/
    <slug>.mp4         최종 영상
    master.wav
    metadata.txt / metadata.json
```

## 10. 테스트

```bash
cd pipeline
python3 -m unittest discover -s tests
```

테스트 80개, ffmpeg 나 AE 없이 돕니다.

- `test_pipeline.py` — 타임라인 계산, 프레임 정렬, NTSC 프레임레이트,
  곡 경계 검출, 유튜브 챕터 규칙, 파일명 파싱, 프로바이더 응답 파싱
- `test_jsx.py` — **AE 스크립트를 실제로 실행해서** 검증합니다.
  `tests/ae_mock/harness.mjs` 가 AE 의 ExtendScript API 를 흉내내므로,
  AE 를 켜지 않고도 이미지 교체·텍스트 입력·컴프 길이·렌더 큐·레이어 순서가
  맞는지 확인할 수 있습니다. 슬롯 복제형과 곡별 컴프형 템플릿을 모두 다룹니다.
  node 가 없으면 자동으로 건너뜁니다.

---

## 알려진 제약

- **Midjourney·Suno 는 공식 API 가 없습니다.** 완전 무인 운영은 불가능하고,
  서드파티 래퍼는 계정 정지 리스크가 있습니다.
- **AE 스크립트는 목업으로만 검증했습니다.** 목업이 실제 AE 와 다르게 동작하는 부분이
  있을 수 있으니, 처음 한 번은 결과 .aep 를 열어서 눈으로 확인하세요.
- **AE 는 GUI 앱을 띄웁니다.** `-noui` 를 줘도 백그라운드 전용은 아니라서,
  렌더 도는 동안 그 PC 에서 AE 로 다른 작업을 하긴 어렵습니다.
- **크로스페이드는 오디오만** 겹칩니다. 화면 전환 효과는 AE 템플릿에서 만드세요.
- **업로드는 자동화하지 않았습니다.** `metadata.json` 이 YouTube Data API 형태에 맞춰
  나오므로, 나중에 붙이려면 그것부터 시작하면 됩니다.

## 출처

- [Suno API Pricing in 2026 — Sunor](https://sunor.cc/blog/suno-api-pricing-2026)
- [Suno API Pricing Explained — EvoLink](https://evolink.ai/blog/suno-api-pricing)
- [AI Image Generation API Pricing: 12 Providers Compared](https://www.digitalapplied.com/blog/ai-image-generation-api-pricing-comparison-2026)
- [Midjourney vs FLUX vs Ideogram v3 (2026)](https://www.aimagicx.com/blog/midjourney-vs-flux-vs-ideogram-image-comparison-2026)
- [Midjourney V8 vs FLUX vs Stable Diffusion (2026) — WaveSpeed](https://wavespeed.ai/blog/posts/midjourney-v8-vs-flux-vs-sora-best-ai-image-generator-2026/)
