# 이마까라 니홍고 RSS 자동 업데이트 설정

## 저장소에 추가할 파일

다음 구조로 파일을 배치합니다.

```text
imakara-rss/
├── .github/
│   └── workflows/
│       └── update-rss.yml
├── imakara_nihongo_cover.jpg
├── imakara_nihongo_rss.xml
├── requirements.txt
└── update_rss.py
```

`AUTOMATION_SETUP.md`는 안내 문서이므로 업로드하지 않아도 됩니다.

## GitHub Pages 설정 변경

1. 저장소의 **Settings → Pages**로 이동합니다.
2. **Build and deployment → Source**를 **GitHub Actions**로 변경합니다.
3. 별도의 템플릿은 선택하지 않습니다. 저장소에 올린 `update-rss.yml`이 배포를 담당합니다.

## 처음 한 번 수동 실행

1. 저장소의 **Actions** 탭을 엽니다.
2. 왼쪽에서 **Update podcast RSS**를 선택합니다.
3. **Run workflow → Run workflow**를 누릅니다.
4. 실행이 끝난 뒤 아래 주소가 정상인지 확인합니다.

```text
https://inspirejihan.github.io/imakara-rss/imakara_nihongo_rss.xml
https://inspirejihan.github.io/imakara-rss/imakara_nihongo_cover.jpg
```

## 실행 일정

워크플로는 매일 `02:00 UTC`, 즉 한국시간 오전 11시에 실행되도록 설정되어 있습니다. GitHub Actions의 예약 실행은 서버 상황에 따라 수 분 정도 늦어질 수 있습니다.

## 동작 방식

- 발행 목록에서 최신 2개 월호와 아직 RSS에 없는 새 월호를 확인합니다.
- 기존 GUID와 비교하여 새 에피소드만 추가합니다.
- 새 음원의 HTTPS 주소, 파일 형식과 실제 바이트 크기를 검증합니다.
- 새 에피소드가 있을 때만 XML을 커밋합니다.
- 실행할 때마다 현재 XML과 표지를 GitHub Pages에 배포합니다.
- 수동 실행도 지원합니다.

## 오류 확인

실패하면 **Actions → Update podcast RSS → 실패한 실행**에서 빨간 단계의 로그를 확인합니다. 팟빵 화면 구조가 변경되었거나 음원 서버가 일시적으로 응답하지 않으면 RSS를 덮어쓰지 않고 작업이 실패하도록 설계되어 있습니다.
