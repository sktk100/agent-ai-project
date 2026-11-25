# HEALTH QUEST

간호 미션룸과 모의상황실습을 연결한 보건간호 문제중심학습 앱입니다. 학생은 단원 질문과 단계별 미션을 진행하고 자신의 판단 근거를 작성합니다.

## Streamlit Community Cloud

이 데모는 저장소의 `main` 브랜치에서 `app_integrated.py`를 실행합니다. Community Cloud는 `requirements.txt`의 패키지를 설치합니다. 외부 자격 증명 없이 실행하면 기본 피드백과 모의실습 데모를 제공합니다.

기존 HEALTH QUEST 배포 앱의 로그인, Firebase 제출·평가, OpenAI RAG 기능은 별도의 서버 설정을 사용합니다. 원본 프로젝트의 [검증된 배포 앱](https://health-quest-firebase-2026.streamlit.app/)에서 볼 수 있습니다.

## 로컬 실행 Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app_integrated.py
```

원본 공개 코드: [HEALTH QUEST](https://github.com/baekkeejin0508-kee/health-tutor-week5).
