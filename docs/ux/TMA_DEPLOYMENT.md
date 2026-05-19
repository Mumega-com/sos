# Telegram Mini App (TMA) Deployment Guide
**Project:** SOS | Empire of the Mind
**Bot:** @EmpireOfTheMindBot (Configure in @BotFather)

## 1. Prerequisites
- Secure HTTPS URL (Use Vercel, Netlify, or ngrok for local dev).
- Telegram Account.

## 2. Register via @BotFather
1. Message `@BotFather` on Telegram.
2. Send `/newbot` -> Name: "Empire of the Mind", Username: `empire_of_the_mind_bot`.
3. Send `/newapp` -> Select your bot.
4. Set Title: "SOS Dashboard".
5. Provide the URL: `https://your-secure-domain.com`.
6. Set Short Name: `sos_deck`.

## 3. Launching
You can now launch the app via:
`https://t.me/empire_of_the_mind_bot/sos_deck`

## 4. UI Features Enabled
- **Auto-Login:** No sign-up required.
- **Haptics:** Physical vibration on "Witness Swipe".
- **Theme Sync:** App background automatically matches Telegram Dark/Light mode (handled via CSS variables).

---
**Status:** TMA Backend & Frontend Bridge Integrated.
