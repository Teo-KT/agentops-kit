# 레벨 2 — 앱을 안 켜도 돌게 만들기 (launchd 상주화)

앱 내장 스케줄러로 몇 주 돌려 보면 한 가지가 걸린다. **앱이 꺼져 있으면 루틴이 안 돈다.** 다음에 앱을 열 때 따라잡기(catch-up)가 되긴 하지만, 새벽에 돌아야 할 일이 아침 열 시에 몰려서 도는 건 다른 문제를 만든다(순서가 뒤집혀 잘못된 보고가 나간다).

이 문서는 그 다음 단계다. macOS 기준으로 쓰지만, 리눅스·윈도우 대응도 마지막에 있다.

---

## 0. 먼저 알아야 할 것: macOS는 순순히 자동화를 허락하지 않는다

이 전환에서 **시간의 90%는 스케줄이 아니라 권한(TCC)에서 소모된다.** 원 시스템이 실제로 부딪힌 순서 그대로 적는다.

### 실패 사례 1 — `exit 127`

launchd에 첫 루틴을 등록했더니 실행되자마자 죽었다. 로그에는 `can't open input file`.

**원인**: launchd가 띄운 셸에는 `~/Documents` 접근 권한이 없다. 스크립트 파일 자체를 열지 못한 것이다.

**교훈 (이게 핵심이다)**:
> **launchd가 직접 실행하는 파일은 `~/Documents`, `~/Desktop`, `~/Downloads` 아래에 두지 않는다.**
> 이 세 폴더는 macOS가 별도 권한으로 보호한다. 실행체는 `~/.claude/scripts/` 같은 보호 대상 밖 경로에 두고, 로그도 마찬가지로 밖에 쌓는다.

### 실패 사례 2 — 권한을 엉뚱한 곳에 줬다

"전체 디스크 접근"을 켰는데도 안 됐다. 권한은 **서버 앱**에 줬는데 정작 파일을 열려던 건 **launchd가 띄운 zsh**였기 때문이다.

**교훈**:
> 전체 디스크 접근은 **실제로 그 파일을 여는 프로세스**에 있어야 한다. 자식 프로세스는 부모의 권한을 상속하므로, **권한을 가진 하나(서버 앱)를 두고 그 자식으로 작업을 돌리는 구조**가 가장 안정적이다.

### 실패 사례 3 — 생성기를 안 고쳤다

스크립트 위치를 옮겨 첫 번째 루틴은 살렸는데, 나머지 열아홉 개가 같은 방식으로 계속 죽었다. plist를 **자동 생성하는 스크립트**를 안 고쳤기 때문이다.

**교훈**: 생성물을 고쳤으면 생성기를 반드시 같이 고친다. 안 그러면 다음 재생성 때 원상복구된다.

---

## 1. 준비 — 한 개만 먼저 옮긴다 (카나리아)

스무 개를 한꺼번에 옮기지 마라. **가장 덜 중요한 루틴 하나**를 골라 launchd로 옮기고 며칠 지켜본다. 원 시스템은 볼트 정리 루틴을 카나리아로 썼다. 실패해도 손해가 없고, 매일 돌아 결과가 빨리 나오는 루틴이 좋다.

카나리아를 옮기는 동안 **앱 쪽 스케줄은 비활성화**한다. 둘 다 켜 두면 하루에 두 번 돈다(중복 방지 장치가 있어도 로그가 지저분해진다).

## 2. 진입점 스크립트

`~/.claude/scripts/run-routine.sh` — 킷의 `web-control-room/bin/run-routine.sh`가 이 파일이다. 하는 일은 세 가지뿐이다.

1. 관제 서버에 "이 루틴 돌려 줘"라고 요청한다
2. 서버가 안 뜨면 서버를 깨우고 최대 6회 재시도한다
3. 그래도 안 되면 **"이번 회차 미실행"으로 로그를 남기고 조용히 끝낸다** — 아침 브리핑이 이걸 결측으로 잡아 준다

> 서버 없이 쓰고 싶다면 1·2번을 지우고 `claude -p "$(cat 루틴프롬프트)"`를 직접 부르면 된다. 다만 **그 경우 `~/Documents` 아래 파일에는 접근하지 못한다**(실패 사례 1). 원장을 홈 디렉터리 바로 아래(`~/AgentOps`)에 두면 이 문제를 통째로 피할 수 있다. **킷의 기본값이 `~/AgentOps`인 이유가 이것이다.**

## 3. plist 생성과 설치

편성표(`AGENTS.md` §1)를 읽어 plist를 자동 생성한다. 시각을 손으로 두 군데 관리하지 않기 위해서다.

```bash
node web-control-room/bin/gen-launchd.js          # 로스터 → plist 생성
web-control-room/bin/install-launchd.sh routine <슬러그>   # 한 개 설치
web-control-room/bin/install-launchd.sh status             # 현황 확인
```

수동으로 만든다면 최소 형태는 이렇다.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agentops.routine.daily-scout</string>
  <key>ProgramArguments</key><array>
    <string>/bin/zsh</string>
    <string>/Users/YOU/.claude/scripts/run-routine.sh</string>
    <string>daily-scout</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/Users/YOU/.claude/logs/daily-scout.out</string>
  <key>StandardErrorPath</key><string>/Users/YOU/.claude/logs/daily-scout.err</string>
</dict></plist>
```

설치·확인·해제:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentops.routine.daily-scout.plist
launchctl kickstart -k gui/$(id -u)/com.agentops.routine.daily-scout   # 지금 즉시 한 번 실행
launchctl list | grep com.agentops                                     # 등록 현황
launchctl bootout gui/$(id -u)/com.agentops.routine.daily-scout        # 되돌리기
```

`launchctl list`의 두 번째 열이 마지막 종료 코드다. **0이 아니면 아직 안 된 것이다.** 첫 등록 뒤에는 반드시 `kickstart`로 한 번 강제 발화시켜 종료 코드를 확인한다. 다음 날 아침까지 기다렸다가 실패를 발견하지 마라.

## 4. 잠든 맥 깨우기

launchd는 잠들어 있던 시각의 작업을 **깨어난 뒤** 실행한다(cron은 그냥 건너뛴다). 새벽에 정시 실행이 필요하면 맥을 미리 깨운다.

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 00:55:00
```

## 5. 이행 순서 (원 시스템이 실제로 밟은 순서)

1. 카나리아 1개 전환 → 앱 스케줄 비활성 → **3일 관찰**
2. 종료 코드 0 + 핸드오프 정상 생성 확인
3. 나머지 전량 전환, 앱 스케줄 일괄 비활성
4. 전환 직후 4~5개를 `kickstart`로 강제 발화해 종료 코드 확인
5. 편성표의 "실행 주체는 이제 launchd" 한 줄을 갱신 — **이걸 안 적으면 반년 뒤 자신이 헷갈린다**

## 6. 전환 후 달라지는 것 하나

헤드리스(`claude -p`)에는 **앱에서 쓰던 도구 일부가 없다.** 원 시스템에서는 스케줄 태스크를 조작하는 도구가 사라져서, 그걸 쓰던 루틴 단계 세 곳을 전부 다른 방법으로 고쳐야 했다(재실행 → 서버 API 호출, 스케줄 목록 대조 → `launchctl list` 대조).

**전환 전에 확인할 것**: 루틴 프롬프트가 앱 전용 기능에 의존하는 곳이 있는가. 있으면 셸 명령이나 파일 기반으로 먼저 바꿔 둔다.

## 7. macOS가 아니라면

| OS | 스케줄러 | 주의 |
|---|---|---|
| Linux | cron 또는 systemd timer | cron은 꺼진 시간대를 건너뛴다. `Persistent=true`인 systemd timer를 권한다 |
| Windows | 작업 스케줄러 | "누락된 작업 가능한 한 빨리 시작" 옵션을 켠다 |

권한 함정은 macOS 특유다. 리눅스·윈도우에서는 이 문서의 0번 절을 통째로 건너뛰어도 된다.

---

## 점검표

- [ ] 실행 스크립트가 `~/Documents` 밖에 있다
- [ ] 로그 경로도 `~/Documents` 밖이다
- [ ] plist 생성기와 생성물이 같은 경로를 가리킨다
- [ ] 카나리아 1개로 3일 관찰했다
- [ ] `kickstart` 후 종료 코드 0을 눈으로 확인했다
- [ ] 앱 스케줄을 껐다 (이중 실행 방지)
- [ ] 편성표에 "실행 주체 = launchd"를 적었다
- [ ] 실패 시 알림 경로가 있다 (없으면 조용히 며칠 안 돈다)
