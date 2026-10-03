# GameVoice Translator

A push-to-talk web application that captures game voice, transcribes it with Gemini, and translates the resulting text for the selected language pair.

## Stack

- **Frontend:** React, TypeScript, and Vite
- **Backend:** FastAPI with WebSockets
- **AI:** Gemini Live speech-to-text and Gemini translation

## Run locally

### 1. Configure the backend

Open PowerShell in `backend` and create a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item ..\.env.example .env
```

Set your own `GEMINI_API_KEY` in `backend/.env`, then start the API:

```powershell
.venv\Scripts\python.exe -m uvicorn main:app --reload
```

### 2. Start the frontend

Open a second PowerShell window in `frontend`:

```powershell
npm install
npm run dev
```

Open the local address printed by Vite (normally `http://localhost:5173`).

## Keeping keys safe

- Never commit `backend/.env` or any `.env` file.
- Use `.env.example` only as a credential-free template.
- If a key is ever exposed, revoke it in Google AI Studio and create a replacement.

## Project documentation

- [Architecture](docs/PRE_M5_ARCHITECTURE.md)
- [Gemini speech-to-text integration](docs/M_REAL_STT.md)
- [M5 translation](docs/M5_TRANSLATION.md)
