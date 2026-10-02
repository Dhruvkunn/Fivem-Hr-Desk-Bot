# Dream City HR Bot

A self-contained Discord HR system for Dream City Government.

## Main workflows
- 📝 Recruitment applications with Discord Modal
- 🎤 Interview scheduling
- ✅ Accept/deny applications
- 👤 Employee records
- 📈 Promotions
- ⚠️ Discipline records
- 🏖️ Leave/LOA
- 🎖️ Certifications
- 🏅 Awards
- 🔄 Transfers
- ⏰ Clock-in/out attendance
- 📊 HR reports
- 🧾 Audit logs
- 🏛️ One-command HR channel/role setup

## Install
Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set the environment variables, or set them directly in your host.

Run:
```bash
python bot.py
```

## Discord permissions
For the first setup, the bot needs permission to create/manage the HR channels and roles. It also needs to be placed **above department/rank roles** if you want it to assign those roles.

Recommended bot permissions: View Channels, Send Messages, Embed Links, Read Message History, Manage Roles, Manage Channels, Use Application Commands.

Do not give Administrator unless you intentionally want to.

## First use
1. Invite the bot with the `bot` and `applications.commands` scopes.
2. Put the bot role above roles it must assign.
3. Run `/setup_hr`.
4. Run `/hr`.
5. Applicants use **Apply**.
6. HR/Command review applications in `#government-applications`.
7. Use **Interview**, **Accept**, or **Deny**.

## Notes
This is the first production foundation. FiveM sync, a web dashboard, configurable rank ladders, granular department permissions, and richer approval workflows should be added as the next layer rather than hard-coded into the initial bot.
