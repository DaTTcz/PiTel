# PiTel ☎

**Dial-up server na Raspberry Pi.** Starý počítač se svým modemem vytočí číslo,
Raspberry Pi s modemem hovor zvedne, naváže PPP spojení a pošle volajícího přes
NAT do LAN a na internet. Jako u poskytovatele internetu v 90. letech.

```
[PC + modem] ── telefon / VoIP ATA ── ústředna ── [modem MT5634ZBA] ── USB ── [Raspberry Pi] ── LAN ── internet
                                                                          mgetty → pppd → NAT
```

PiTel nic neprogramuje znovu. Spojuje osvědčené nástroje (**mgetty**, **pppd**,
**nftables**) a přidává k nim:

- **instalátor**, který na čistém Raspberry Pi OS Lite všechno nainstaluje a nastaví,
- **webovou správu**: stav linky, rychlost spojení, živý graf provozu, historie hovorů,
  uživatelé, nastavení, AT příkazy pro modem, logy a aktualizace jedním tlačítkem,
- **příkaz `pitel`** pro konzoli (a krátký stav po přihlášení přes SSH).

Je napsaný v Pythonu jen se standardní knihovnou, takže nepotřebuje pip ani webový server.

---

## Požadavky

- Raspberry Pi (testováno pro Pi 2) s **Raspberry Pi OS Lite** (32bit stačí)
- USB modem, který se ukáže jako `/dev/ttyACM0` nebo `/dev/ttyUSB0`
  (např. MultiTech MT5634ZBA-USB)
- připojení Raspberry Pi do LAN kabelem (`eth0`)
- telefonní linka do modemu: analogová pobočka ústředny nebo VoIP převodník (ATA)

## Instalace

Na Raspberry Pi:

```bash
sudo apt install -y git
git clone https://github.com/DaTTcz/PiTel.git
cd PiTel
sudo bash install.sh
```

Nebo jedním příkazem:

```bash
curl -fsSL https://raw.githubusercontent.com/DaTTcz/PiTel/main/install.sh | sudo bash
```

Instalátor:

1. doinstaluje `mgetty ppp nftables git python3`,
2. nakopíruje aplikaci do `/opt/pitel` a vytvoří příkaz `pitel`,
3. vypne ModemManager, pokud běží (zabíral by modem),
4. spustí **průvodce**, který se zeptá na modem, typ linky, maximální rychlost, IP adresy,
   DNS, prvního uživatele pro připojení a heslo do webové správy,
5. vygeneruje konfiguraci a spustí služby `pitel-mgetty`, `pitel-nat` a `pitel-web`.

Pak otevři v prohlížeči `http://<IP-raspberry>/`.

Průvodce jde kdykoliv spustit znovu příkazem `sudo pitel setup` nebo `sudo bash install.sh --reconfigure`.

## Aktualizace

Ve webu: **Systém → Stáhnout a nainstalovat aktualizaci**, nebo v konzoli:

```bash
sudo pitel update
```

PiTel hledá nové verze podle **git tagů** ve tvaru `v1.2.3`. Web se na GitHub podívá po
startu a pak každých 6 hodin. Když najde vyšší verzi, ukáže vedle čísla verze žlutý odznak
`⬆ v1.2.3` a nová verze se objeví i v `pitel status`. Ručně se dá zkontrolovat tlačítkem
**Zkontrolovat teď** nebo příkazem `pitel update --check`.

Aktualizace stáhne tagy a přepne se na nejvyšší z nich, znovu vygeneruje konfiguraci a
restartuje služby. Probíhající hovor se nepřeruší, mgetty se v takovém případě restartuje
až po zavěšení. V Nastavení jde přepnout na „poslední commit z větve“ (pro testování).

### Vydání nové verze

1. Uprav `__version__` v `pitel/__init__.py` (např. `0.2.0`), commitni a pushni.
2. Vytvoř tag a pushni ho:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```
   V GitKrakenu: pravým tlačítkem na commit → *Create tag here* → `v0.2.0`,
   pak pravým na tag → *Push v0.2.0 to origin*.

Commity bez tagu se na Raspberry Pi nedostanou, dokud je nevydáš.

## Příkaz `pitel`

| Příkaz | Co dělá |
|---|---|
| `pitel` / `pitel status` | přehled: modem, služby, stav linky, aktuální hovor, statistika |
| `pitel watch` | živý přehled s rychlostí přenosu (obnovuje se každou sekundu) |
| `pitel calls` | historie hovorů |
| `pitel users` | seznam uživatelů |
| `sudo pitel users add JMÉNO [HESLO]` | přidat uživatele / změnit heslo |
| `sudo pitel users del JMÉNO` | smazat uživatele |
| `pitel config` / `sudo pitel config set KLÍČ HODNOTA` | zobrazit / změnit nastavení |
| `sudo pitel hangup` | ukončit probíhající hovor |
| `sudo pitel restart` | restart mgetty |
| `sudo pitel modem ATI0 AT\&V` | poslat AT příkazy modemu (jen když neprobíhá hovor) |
| `pitel logs` | logy mgetty a pppd |
| `sudo pitel admin-password` | změnit heslo do webu |
| `pitel update --check` | zjistit, jestli je na GitHubu nová verze |
| `sudo pitel update` | nainstalovat nejnovější verzi z GitHubu |

## Jak to funguje

```
mgetty (pitel-mgetty.service) hlídá /dev/ttyACM0
  │ zazvoní → ATA → CONNECT 14400/V32B/LAPM
  ├─ volající pošle PPP rámec  → /AutoPPP/ → libexec/ppp-wrapper → pppd
  │       pppd ověří jméno/heslo (PAP, CHAP, MS-CHAPv2), přidělí IP 192.168.50.2
  │       ip-up / ip-down zapisují stav do /run/pitel, po zavěšení záznam do historie
  └─ volající napíše jméno na „login:“ → libexec/line-test (testovací echo linky)

pitel-nat.service  nftables: masquerade klienta ven přes eth0, klient nemá přístup do správy
pitel-web.service  webová správa (Python http.server), port 80
```

| Soubor | Obsah |
|---|---|
| `/etc/pitel/config.json` | nastavení |
| `/etc/pitel/users.json` | uživatelé (600) |
| `/etc/pitel/admin.json` | hash hesla do webu (600) |
| `/var/lib/pitel/calls.jsonl` | historie hovorů |
| `/etc/mgetty/*.config`, `/etc/pitel/ppp-options`, `/etc/pitel/nat.nft` | generované (neupravovat ručně) |

Originální `/etc/mgetty/mgetty.config` a `login.config` se při prvním přepsání zálohují jako `*.pitel.orig`.

## Modem a linka přes VoIP

Dva analogové modemy se spolu domluví nejvýš na **V.34 (33 600 b/s)**. V.90/V.92 vyžaduje
digitální stranu u poskytovatele. Přes VoIP bývá stabilních **9 600–14 400 b/s**, proto PiTel
podle typu linky nastaví omezení přes `AT+MS=<modulace>,1,300,<max>`.
*Syntaxi `AT+MS` ověř v manuálu svého modemu. Vlastní AT příkazy jde přidat v Nastavení.*

Nastavení VoIP převodníku (ATA) na obou stranách:

- kodek **jen G.711** (PCMA/PCMU), žádný G.729 apod.,
- **vypnout VAD** / detekci ticha a comfort noise,
- **vypnout echo cancelation**, případně zapnout „modem/fax passthrough“,
- **pevný jitter buffer**,
- **T.38 vypnout** (je jen pro fax).

## Nastavení na straně volajícího (Windows)

1. *Ovládací panely → Centrum síťových připojení → Nastavit nové připojení → Připojit k internetu → Telefonické připojení*
2. Číslo, jméno a heslo podle uživatele v PiTelu.
3. Ve vlastnostech připojení, záložka *Zabezpečení*, povol CHAP / MS-CHAP v2 (nebo PAP).

**Test linky bez PPP:** v terminálu (HyperTerminal, PuTTY na COM port) vytoč `ATDT číslo`.
Po `CONNECT` napiš cokoliv na výzvu `login:`, spustí se testovací echo, které vrací napsaný text.

## Řešení problémů

- **Modem nenalezen** → `lsusb`, `dmesg | grep tty`, zkontroluj zařízení v Nastavení.
- **Zvoní, ale nic** → `pitel logs`, zkus `sudo pitel modem ATI0` (modem odpovídá?).
- **Spojí, ale padá** → sniž max. rychlost (14400 → 9600), zkontroluj nastavení ATA.
- **„Klient se neověřil“** → jméno/heslo, ve Windows povol PAP/CHAP.
- **Připojeno, ale nejde internet** → `sudo nft list table ip pitel`,
  `sysctl net.ipv4.ip_forward`, rozhraní do LAN v Nastavení.

## Vývoj

Bez Raspberry Pi jde všechno zkoušet v „testovacím kořeni“. Systémové příkazy se jen vypíšou:

```bash
export PITEL_PREFIX=/tmp/fakeroot
mkdir -p $PITEL_PREFIX/dev && touch $PITEL_PREFIX/dev/ttyACM0
bin/pitel admin-password          # heslo do webu
bin/pitel config set web_port 8080
bin/pitel web     # http://localhost:8080/
```

## Licence

MIT
