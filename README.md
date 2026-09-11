# CarWatch

Hlídač ojetých SUV pro **Sauto + TipCars + Bazoš Auto**. Ukládá nabídky do SQLite, filtruje je podle modelu/ceny/roku/nájezdu, počítá score a posílá na Telegram pouze nové odpovídající inzeráty. Změny ceny si ukládá do databáze, ale ve výchozím nastavení je neposílá.

## 1. Telegram – první nastavení

1. V Telegramu otevři `@BotFather` a vytvoř bota přes `/newbot`.
2. Napiš svému novému botovi libovolnou zprávu (např. `test`).
3. V prohlížeči otevři `https://api.telegram.org/botTVUJ_TOKEN/getUpdates` a najdi `chat -> id`.
4. V této složce zkopíruj `.env.example` na `.env` a doplň:

```env
TELEGRAM_BOT_TOKEN=token_od_BotFather
TELEGRAM_CHAT_ID=123456789
```

Token nikam veřejně necommituj; `.env` je v `.gitignore`.

### Druhý Telegram chat

Pro posílání stejných inzerátů také do druhého chatu doplň do `.env`:

```env
TELEGRAM_BOT_TOKEN_2=token_druheho_bota
TELEGRAM_CHAT_ID_2=987654321
```

Když používáš stejného bota pro oba chaty, dej do `TELEGRAM_BOT_TOKEN_2` stejný
token jako u prvního bota. Druhý příjemce je volitelný a bez obou hodnot se
ignoruje. Samostatné bot ID se nikam nezadává: bota určuje jeho token a cílovou
konverzaci určuje chat ID.

Chceš-li novému chatu jednorázově poslat poslední neprázdnou dávku nalezenou v
databázi, použij:

```bash
python main.py --resend-latest-to druhy-chat
```

Pokud hlídač běží na GitHubu, otevři **Actions → CarWatch → Run workflow** a u
volby **Znovu poslat poslední nalezenou dávku** vyber `druhy-chat`. Tento režim
nespouští nové hledání a neposílá dávku znovu do hlavního chatu.

### Ověření Telegramu

Na Macu můžeš dvakrát kliknout na `test-telegram.command`, nebo v Terminálu:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # pouze poprvé; pak .env vyplň
python main.py --test-telegram
```

Pokud je vše správně, přijde zpráva:

`✅ CarWatch je připojený (hlavni-chat). Telegram upozornění fungují.`

## 2. První načtení bez záplavy zpráv

Nejdřív existující inzeráty pouze uložíme:

```bash
source .venv/bin/activate
python main.py --seed
```

`--seed` **neposílá Telegram notifikace**.

## 3. Spuštění hlídače

```bash
python main.py
```

Výchozí interval je 30 minut. Jednorázová kontrola:

```bash
python main.py --once
```

Na Macu můžeš také dvakrát kliknout na `run.command`. Při prvním spuštění vytvoří `.venv`; pokud chybí `.env`, vytvoří ho z šablony a otevře k vyplnění.

## Co se hlídá

- max. cena **550 000 Kč**
- naftová auta jsou vyřazená
- auta s manuální převodovkou jsou vyřazená
- mainstream: od 2019, ideál 2020+, max. 130 000 km
- Volvo XC60 / Lexus NX: od 2018, max. 140 000 km
- whitelist: RAV4, CX-5, CR-V, Outlander, Tucson, Santa Fe, Kuga, Sportage, Sorento, 5008, Tiguan, Kodiaq, Koleos, X-Trail, Forester, XC60, NX
- Peugeot 3008 a Škoda Karoq nejsou sledované

Každá nabídka dostane score 0–100. Telegram ve výchozím nastavení posílá **jen nové odpovídající nabídky**. Změny ceny se dál evidují, ale notifikace jsou vypnuté (`notifications.price_changes: false`).

## Poznámky ke zdrojům

Scrapery používají veřejné výsledkové stránky a záměrně nepoužívají obcházení přihlášení, CAPTCHA ani anti-bot ochrany. Bazoš obsahuje hodně dílů a příslušenství, proto jeho parser přijímá jen inzeráty, kde zároveň rozpozná cenu, rok a nájezd. Weby mohou časem měnit HTML; každý zdroj je proto samostatný adaptér v `scrapers/`.


## GitHub Actions – běh bez zapnutého Macu

Projekt obsahuje `.github/workflows/car-watch.yml`. GitHub ho spustí dvakrát denně (07:00 a 16:00 UTC; v českém letním čase přibližně 09:00 a 18:00).

1. Vytvoř si na GitHubu nový privátní repository a nahraj do něj obsah složky `car-watch`. Soubor `.env` na GitHub nenahrávej.
2. V repository otevři **Settings → Secrets and variables → Actions → New repository secret**.
3. Přidej secrets přesně pod těmito názvy:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TELEGRAM_BOT_TOKEN_2` (pro druhý chat)
   - `TELEGRAM_CHAT_ID_2` (pro druhý chat)
4. Otevři záložku **Actions → CarWatch → Run workflow** a spusť ho ručně poprvé.

První běh pouze uloží současné odpovídající inzeráty (`--seed`) a **nic neposílá**. Databáze se mezi běhy uchovává pomocí GitHub Actions cache. Každý další běh porovná aktuální nabídku s uloženou databází a pošle jen inzeráty, které předtím nebyly vidět.

> Pozn.: GitHub plánované workflow může mít několikaminutové zpoždění; pro hlídání 2× denně to nevadí.

## AI hodnocení detailu (nové)

CarWatch nyní může u nových inzerátů otevřít detail, vytáhnout viditelný text a detekovanou výbavu a požádat OpenAI model o strukturované hodnocení 0–100. AI dostává i medián ceny/nájezdu/roku právě nalezených aut stejného modelu.

Do lokálního `.env` přidej:

```env
OPENAI_API_KEY=tvuj_api_klic
```

Na GitHubu přidej třetí Repository secret `OPENAI_API_KEY`. Pokud klíč chybí nebo API selže, CarWatch nespadne a použije lokální fallback score.

Po aktualizaci kódu spusť **Actions → CarWatch → Run workflow → main**.
Tlačítko **Re-run jobs** u staršího běhu opakuje jeho původní commit, takže
nepoužije nově přidanou AI implementaci. Workflow předává `OPENAI_API_KEY`
aplikaci a při chybějícím secretu zobrazí varování a poznámku v souhrnu běhu.
Přítomnost klíče sama o sobě nepotvrzuje funkčnost API; úspěšná hodnocení
jsou v logu označená `[AI]`, chyby jako `[WARN] AI evaluation failed`.

Jednorázové poslání aktuální TOP 20:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python main.py --send-all
```

`--send-all` vezme podle `config.yml` předvýběr 50 aut, otevře jejich detail, nechá je ohodnotit AI a odešle 20 nejlepších. Běžný GitHub Actions běh se nemění: jede 2× denně a AI hodnotí pouze nové inzeráty.

## Jak scraper funguje – stručně

1. `main.py` načte `.env` a `config.yml` a spustí zapnutá hledání ze sekce `searches`.
2. Adaptéry v `scrapers/` stáhnou výsledkové stránky Sauto, TipCars a Bazoš a převedou inzeráty na jednotný formát.
3. `matcher.py` vyřadí auta mimo povolené modely, cenu, rok a nájezd a přidělí jim lokální score.
4. `db.py` porovná výsledek se SQLite databází a označí nový inzerát nebo změnu ceny. Díky tomu se stejný inzerát neposílá opakovaně.
5. U nových aut může `ai_ranker.py` otevřít detail a s `OPENAI_API_KEY` doplnit AI score a krátké shrnutí; bez klíče zůstane lokální hodnocení.
6. `notifier.py` vytvoří zprávu a přes Telegram Bot API ji pošle všem nakonfigurovaným příjemcům. `TELEGRAM_BOT_TOKEN` určuje odesílajícího bota, `TELEGRAM_CHAT_ID` cílový chat; varianty s `_2` slouží druhému příjemci.
7. Lokálně se kontrola opakuje podle `interval_minutes`; na GitHubu ji spouští plán v `.github/workflows/car-watch.yml` a databáze se mezi běhy obnovuje z cache.
