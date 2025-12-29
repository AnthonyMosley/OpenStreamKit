# OpenStreamKit

**OpenStreamKit** is a local-first, open-source stream automation toolkit built for creators who want full control over their streaming workflow.

It runs entirely on your own machine, listens to live stream events, and lets you react to them however you want — with code today, and with a UI in the future.

No cloud lock-in. No proprietary logic. Fully moddable.

---

## ✨ What is OpenStreamKit?

OpenStreamKit is an **event-driven automation engine** for live streaming.

It currently focuses on:
- Receiving live events from streaming platforms (Kick first)
- Processing those events locally
- Triggering logic based on chat, follows, subscriptions, and more

The long-term goal is to provide:
- A desktop UI for configuration and control
- Native OBS integration
- A visual automation system for non-programmers
- A plugin/mod system for advanced users

---

## 🧠 Core Principles

- **Local-first**  
  Everything runs on your machine. You own the server, the data, and the logic.

- **Open source by design**  
  All code is readable, editable, and extendable.

- **Beginner-readable code**  
  The project is intentionally structured and commented so newcomers can follow along.

- **Event-driven architecture**  
  Stream events are inputs. What happens next is entirely up to you.

---

## 🛠 Current Features

- Kick OAuth authentication
- Webhook-based event handling
- Live chat message detection
- Follow event detection
- Persistent token storage
- Structured logging with optional color output
- Debug snapshot system (raw webhook payloads saved locally)

---

## 🔮 Planned Features

- OBS WebSocket integration
- Desktop UI to start/stop and configure the server
- Visual automation editor
- Multi-platform support (Kick first, others later)
- Plugin / mod support
- Rule-based triggers and actions
- Shareable automation configurations

---

## 📂 Project Structure

The codebase is intentionally kept in a single, readable flow:

- **Imports** – dependencies only  
- **Config / Constants** – environment, paths, flags  
- **Helper Functions** – small reusable logic  
- **Startup Logic** – runs once on launch  
- **Event Handlers / Routes** – where behavior lives  

Runtime JSON artifacts (tokens, webhook snapshots) are written to a local `json/` directory and ignored by git.

---

## 🚀 Getting Started

### 1️⃣ Clone the repository
```bash
git clone https://github.com/AnthonyMosley/OpenStreamKit.git
cd OpenStreamKit
```

### 2️⃣ Create a virtual environment
```bash
python -m venv .venv
```

Activate it:

**Windows**
```powershell
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux**
```bash
source .venv/bin/activate
```

### 3️⃣ Install dependencies
```bash
pip install -r requirements.txt
```

### 4️⃣ Configure environment variables
Create a `.env` file in the project root:

```env
KICK_CLIENT_ID=your_client_id
KICK_CLIENT_SECRET=your_client_secret
KICK_WEBHOOK_PUBLIC_URL=https://your-public-url
```

### 5️⃣ Run the server
```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Debugging

When `DEBUG_PAYLOADS=1` is set, OpenStreamKit will save raw webhook payloads to the `json/` directory for inspection.

This makes it easy to:
- Understand incoming event structures
- Develop new handlers
- Debug edge cases without terminal spam

---

## 🤝 Contributing

This project is intentionally beginner-friendly.

- Small, focused contributions are welcome
- Code clarity is valued over cleverness
- If you can explain *why* a change exists, it belongs here

Issues, discussions, and pull requests are encouraged.

---

## 📜 License

OpenStreamKit is released as **free and open-source software**.

You are encouraged to:
- Use it
- Modify it
- Extend it
- Learn from it

A formal license file will be added before the first tagged release.

---

## ❤️ Philosophy

OpenStreamKit exists because creators deserve tools they **own**, **understand**, and **control**.

If you want automation without surrendering your workflow, this is for you.
