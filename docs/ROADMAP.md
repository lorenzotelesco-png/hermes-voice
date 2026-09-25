# Hermes Hub: da assistente vocale a gestionale

Piano del 2026-09-25. Punto di partenza: branch `modernize/hermes-2026-09` (`0c8f0ae`),
assistente vocale funzionante, 2.84 s end-to-end misurati dal telefono.

Obiettivo: una sola PWA sul telefono per parlare e scrivere con Hermes, rileggere le
trascrizioni, controllare il server, gestire i file e, alla fine, leggere e rispondere a
tutte le chat personali (WhatsApp, Instagram, WeChat, ...) da un unico posto.

---

## 1. Cosa cambia il piano

**Il backend del gestionale esiste già.** La dashboard di Hermes (porta 9119, già attiva
sul VPS) espone via REST quasi tutto quello che serve, e l'API server (porta 8642) ha una
chat nativa con la cronologia tenuta lato server:

| Serve al gestionale | Endpoint già presente (verificato sulla versione installata) |
|---|---|
| Chat con storico lato server, eventi dei tool | `POST /api/sessions/{id}/chat/stream` (API server) |
| Stop di un turno, approvazione di un comando | `POST /v1/runs/{id}/stop`, `POST /v1/runs/{id}/approval` |
| Elenco sessioni di tutti i canali, ricerca full-text | `GET /api/sessions`, `GET /api/sessions/search` |
| Trascrizione completa di una sessione | `GET /api/sessions/{id}/messages` |
| CPU, RAM, disco, uptime | `GET /api/system/stats` |
| Stato gateway e piattaforme collegate | `GET /api/status` |
| Log con filtro per livello | `GET /api/logs` |
| Costi e token | `GET /api/analytics/usage` |
| Cron: lista, pausa, avvio manuale | `/api/cron/jobs/*` |
| Riavvio gateway | `POST /api/gateway/restart` |
| File: lista, lettura, scrittura, upload | `/api/fs/list`, `/api/fs/read-text`, `/api/fs/write-text`, `/api/files/upload` |

Quindi le fasi 1-3 sono quasi tutte **frontend mobile e un proxy sottile e sicuro**.
L'unico backend davvero nuovo è l'inbox unificata (fase 4).

**"Integrare le chat" sono due cose diverse**, e Hermes ne fa solo una:

- *Parlare con Hermes da WhatsApp, WeChat, Telegram...*: il gateway di Hermes lo fa già.
  È un bot: vede solo i messaggi indirizzati a lui.
- *Leggere e rispondere alle tue chat personali da un'unica app*: Hermes non lo fa.
  Servono dei bridge, ed è la fase 4. È questa quella che hai chiesto.

## 2. Cosa costruire e cosa no

Costruisco nella PWA solo quello che usi **dal telefono, spesso**. Per il resto c'è
già la dashboard completa, raggiungibile da un link nella tab "Altro".

| Nella PWA | Resta nella dashboard di Hermes |
|---|---|
| Chat voce + testo, trascrizioni, approvazioni | Editor di `config.yaml`, chiavi API |
| Stato server, log, cron, costi, alert | Skill, MCP, profili, webhook, pairing |
| Vault Obsidian e file di lavoro | Configurazione dei canali del gateway |
| Inbox unificata | Chat TUI completa (terminale nel browser) |

## 3. Architettura

```
iPhone (PWA "Hub")
   │  HTTPS via ngrok, token + cookie firmato (accesso invariato, decisione del 2026-09-25)
   ▼
Hub backend  (FastAPI, 127.0.0.1:5000, utente non-root)
   ├─► API server Hermes :8642     chat/stream, runs, approvazioni
   ├─► Dashboard Hermes  :9119     sessioni, file, stato, log, costi, cron
   ├─► Beeper                      inbox unificata (fase 4, integrazione da progettare)
   ├─► STT/TTS                     client-direct come oggi
   └─► SQLite                      audit log, iscrizioni push, preferenze
```

Scelte e perché:

- **Backend da Flask a FastAPI.** Servono WebSocket (eventi live della dashboard), molti
  stream SSE aperti insieme (chat, log, inbox) e un client Matrix, che è asyncio.
  È lo stesso stack di Hermes. Oggi il server è 555 righe con 3 test e gira con il server
  di sviluppo di Flask come root: portarlo ora costa poco, dopo tre fasi no.
- **Frontend da vanilla JS a Vite + Preact + TypeScript.** Cinque sezioni con stato
  condiviso non stanno in un `app.js` da 500 righe. Preact ha l'API di React (quella che
  gli agenti scrivono meglio) in 4 KB. Il modulo voce (VAD, TTS in streaming, barge-in)
  viene portato **così com'è**, senza framework dentro.
- **Inbox con Beeper** (decisione del 2026-09-25), dettagli alla fase 4 e al punto 6.

## 4. Vincoli verificati sul VPS (2026-09-25)

- **Risorse**: 3 vCPU, 5.7 GiB RAM (2.6 disponibili), disco 59 GB con 16 liberi (73%).
  Basta per le fasi 0-3 e per iniziare la 4. Il disco va tenuto d'occhio: i media delle
  chat crescono.
- **Esposizione**: il firewall scarta tutto su `eth0` tranne poche regole, quindi
  8642/9119/5000 non sono pubbliche. L'unica superficie pubblica è **ngrok** verso la
  porta 5000, protetta da token e cookie firmato. Resta così per scelta: l'accesso non
  deve dipendere da Tailscale.
  Ci sono anche due regole `accept` per 8081 e 3000 senza nessun servizio in ascolto:
  residui da pulire.
- **Hermes installato**: commit del 6 settembre, 19 giorni indietro rispetto a upstream.
  Gli endpoint della tabella al punto 1 ci sono già.
- **Docker** installato (gira Portainer): comodo per lo stack Matrix della fase 4.

## 5. Fasi

Stime in giorni di lavoro effettivo con agenti, non di calendario.

### Fase 0: fondamenta (3-5 giorni)

Prima di tutto, perché ogni fase successiva dà all'app più potere: file, terminale,
server, messaggi privati. Un token rubato oggi apre l'assistente vocale; alla fine della
fase 5 aprirebbe tutto.

1. **Chiudere il lavoro sulla voce**: PR e merge di `modernize/hermes-2026-09` su `main`,
   debiti al punto 10. L'Hub parte da `main` pulito.
2. **Aggiornare Hermes** e fissarne la versione. Scrivere uno script di *contract test*
   che chiama ogni endpoint usato dall'Hub e controlla la forma delle risposte, da
   rilanciare dopo ogni `hermes update`. È l'assicurazione contro gli aggiornamenti.
3. **Accesso invariato** (decisione del 2026-09-25: niente vincolo a Tailscale). Stesso
   URL ngrok, stesso token, stesso cookie: il telefono resta collegato senza rifare il
   login. Rinforzi che non cambiano nulla per chi usa l'app: Hub solo su
   `127.0.0.1:5000`, POST accettati solo dalla pagina dell'app stessa, niente CORS,
   log degli accessi spento (il `?k=` finiva nel journal in chiaro).
4. **Backend FastAPI** con gli stessi endpoint di oggi spostati sotto `/api`, test
   portati, utente di sistema dedicato `hermes-hub`, unit systemd con il sistema in sola
   lettura, **audit log** di ogni azione di scrittura (cosa, quando).
5. **Scheletro frontend**: Vite + Preact + TS, barra in basso con le tab
   Chat / Server / File / Altro (Inbox arriva con la fase 4), modulo voce portato,
   `index.html` mai in cache (resta valido il fix `b1ff51b`). Il service worker slitta
   alla fase 2, dove serve per le notifiche push: prima non avrebbe nulla da fare.
6. **Spazio**: rimuovere Ollama (1.9 GB) e i modelli Piper duplicati. Prima va spostata
   la compressione del contesto di Hermes (`auxiliary.compression`), che punta ancora a
   Ollama anche se il servizio è spento: con le sessioni lunghe della fase 1 fallirebbe.
   *Fatto: compressione spostata, Ollama rimosso. I modelli Piper duplicati se ne vanno
   con `/root/hermes-voice`, vedi punto 10.*

**Fatto quando**: la voce funziona dallo stesso URL di sempre con prima frase entro +10%
di oggi (misurata con "Tempi sullo schermo" in Altro); il telefono non deve rifare il
login; i test passano; il contract check passa prima e dopo l'aggiornamento di Hermes.

### Fase 1: chat unificata (4-6 giorni)

Voce e testo nella stessa conversazione, con la trascrizione sempre visibile.

1. **Da stateless a sessione lato server.** Oggi il client manda gli ultimi 8 messaggi a
   `/v1/chat/completions`: il contesto si ferma lì. L'Hub passa a
   `/api/sessions/{id}/chat/stream`, dove Hermes tiene tutta la cronologia e manda gli
   eventi `assistant.delta`, `tool.started`, `tool.completed`, `run.completed`.
   Il chunking per frasi del TTS resta uguale, applicato agli `assistant.delta`.
   **Da misurare prima di migrare**: la latenza della prima frase sui due endpoint, con
   misure alternate come abbiamo fatto finora. Se il nuovo è più lento, la voce resta su
   chat completions e il testo usa le sessioni.
2. **UI chat**: bolle con Markdown; ogni turno vocale compare come testo (quello che hai
   detto e quello che ha risposto); schede per i tool ("sta usando il terminale...");
   tasto stop; campo di testo e microfono nello stesso thread.
3. **Sessioni**: elenco di tutte le sessioni (voce, Discord, cron, CLI) con icona della
   sorgente, ricerca full-text, apertura della trascrizione, possibilità di continuare
   una sessione qualunque.
4. **Approvazioni sul telefono**: quando Hermes vuole eseguire un comando che richiede
   conferma, compare un foglio con il comando esatto e i tasti Approva / Nega
   (`/v1/runs/{id}/approval`). È ciò che rende sicuro dare il terminale all'assistente,
   e la fase 5 lo riusa per l'invio dei messaggi.
5. **Togliere il mirroring su Discord** delle sessioni vocali: con le trascrizioni
   nell'app diventa un doppione (vedi decisioni).

**Fatto quando**: una conversazione iniziata a voce si continua scrivendo e viceversa;
dopo 20 turni Hermes ricorda il primo; un comando che richiede conferma si approva dal
telefono; latenza della prima frase entro +10%.

### Fase 2: server (3-4 giorni)

1. **Stato**: servizi (agent, dashboard, Hub, poi lo stack Matrix), CPU/RAM/disco,
   piattaforme del gateway collegate o no, costo di oggi e degli ultimi 7 giorni.
2. **Log** filtrabili, con aggiornamento automatico.
3. **Cron**: elenco, pausa, ripresa, avvio manuale.
4. **Azioni**: riavvio del gateway e dei servizi di una allowlist, sempre con conferma e
   audit log. Lo stato systemd lo legge l'Hub con una regola sudoers limitata ai soli
   `systemctl is-active` / `restart` di quelle unit.
5. **Notifiche push** (Web Push, supportato dalle PWA installate da iOS 16.4): servizio
   giù da più di 2 minuti, disco oltre l'85%, RAM disponibile sotto 300 MB, riavvio per
   OOM, cron fallito, e dalla fase 4 bridge disconnesso.

**Fatto quando**: fermando a mano un servizio arriva la notifica sul telefono entro
3 minuti, anche dalla Cina; il riavvio dall'app funziona e resta nell'audit log.

### Fase 3: file e vault (3-4 giorni)

1. **Radici consentite** decise dall'Hub, non dalla dashboard (che vede tutto il disco
   come root): vault Obsidian in lettura e scrittura; repo e log di Hermes in sola lettura.
2. **Note**: rendering Markdown con i wiki-link funzionanti, modifica e salvataggio.
3. **Git come annulla**: ogni salvataggio fa `pull --rebase`, commit e push. Se c'è un
   conflitto con Obsidian sul PC, l'Hub si ferma e lo mostra, non forza mai.
4. **Ricerca** nel vault e **upload** di foto e PDF dal telefono negli allegati.
5. **Cattura rapida**: una riga nella nota giornaliera con un tocco o a voce.

**Fatto quando**: una nota modificata dal telefono compare in Obsidian sul PC dopo il
suo pull; un conflitto simulato viene segnalato senza perdere nessuna delle due versioni.

### Fase 4: inbox unificata con Beeper (da progettare)

**Deciso il 2026-09-25: Beeper**, poi un modo per includere anche WeChat. Beeper
collega già WhatsApp, Instagram, Telegram, Messenger, Signal e altri; WeChat no.

Da progettare prima di iniziare:

1. **Dove gira Beeper per l'Hub.** La Desktop API e il server MCP di Beeper stanno dentro
   Beeper Desktop e rispondono solo sulla macchina dove gira. Le strade: Beeper Desktop
   sul PC Windows (che però non è sempre acceso); Beeper Desktop sul VPS senza schermo
   (da verificare se regge); oppure i bridge Beeper self-hosted sul VPS con `bbctl`, che
   però passano dai server di Beeper con cifratura end-to-end da implementare nell'Hub.
2. **Cosa mostra l'Hub**: una tab Inbox propria, oppure solo Hermes che lavora sui
   messaggi (fase 5) mentre per leggere si usa l'app Beeper.
3. **WeChat**: vedi il punto 6. Da cercare una strada che non metta a rischio l'account.

**Fatto quando** (da confermare nel progetto): un messaggio WhatsApp è leggibile e
rispondibile da dove si è deciso al punto 2; Instagram dopo due settimane di WhatsApp
stabile.

### Fase 5: Hermes sull'inbox (5-7 giorni)

1. **Strumenti inbox per Hermes**: lettura (non letti, ricerca, lettura di una chat,
   bozza di risposta) e scrittura (invio). Candidato naturale: il server MCP già
   incluso in Beeper Desktop. Registrato in Hermes come `trust: untrusted`: così ogni
   invio genera una richiesta di approvazione che compare sul telefono con il foglio
   della fase 1. Nessun messaggio parte senza un tuo tocco.
2. **A voce**: "leggimi i messaggi non letti", "cosa mi ha scritto Julia?", "rispondi a
   Marco che arrivo alle 8": Hermes legge la bozza, chiede conferma, poi invia.
3. **Riepilogo del mattino** con un cron di Hermes alle 8 ora di Shanghai: chat non lette
   e cose che richiedono risposta, come notifica e nella nota giornaliera.
4. **Isolamento dal prompt injection.** I messaggi in arrivo sono testo scritto da altri
   che finisce nel contesto di un agente con accesso al terminale. Un messaggio che dice
   "ignora le istruzioni ed esegui..." è un attacco reale. Mitigazioni:
   - un **profilo Hermes dedicato all'inbox**, senza terminale, file o web: legge e
     riassume ma non può agire;
   - il profilo principale non vede i messaggi, gli arriva solo il riassunto;
   - approvazione manuale per il terminale finché l'inbox è attiva.
   Il rischio non si elimina, si confina.

**Fatto quando**: i casi a voce sopra funzionano; un invio senza approvazione viene
bloccato sempre; un messaggio di test con istruzioni malevole non produce nessuna
chiamata al terminale.

## 6. Piattaforme: cosa è fattibile

| Piattaforma | Metodo | Rischio | Limiti | Scelta |
|---|---|---|---|---|
| WhatsApp | `mautrix-whatsapp`, dispositivo collegato come WhatsApp Web | Basso per uso personale; è lo stesso metodo che usa Beeper | Il telefono deve connettersi almeno ogni 14 giorni, o i dispositivi collegati si scollegano | ✅ Primo |
| Instagram | `mautrix-meta` in modalità Instagram (sessione web) | Medio: automazione non ufficiale, contro i termini di Meta | Possibili verifiche di sicurezza al login | ✅ Secondo |
| Instagram, API ufficiale | Instagram API con login Instagram | Nessuno | L'account deve diventare professionale, e **un account professionale non può essere privato**; risposte solo entro 24 h dall'ultimo messaggio dell'altro; serve un webhook pubblico | ❌ |
| Telegram, Signal, Messenger, Discord, LinkedIn | bridge mautrix corrispondente | Basso-medio | — | Solo quelli che usi |
| WeChat, chat personali | Solo hook sul client Windows (WeChatFerry) o servizi sul protocollo iPad | **Alto**: gli hook sono un motivo tipico di ban, più severo per gli account stranieri | PC Windows sempre acceso con WeChat aperto; il progetto stesso si dichiara "solo a scopo di studio" | ❌ Vedi sotto |
| WeChat, parlare con Hermes | Adattatore Weixin di Hermes via iLink, ufficiale Tencent | Nessuno | È un bot separato: niente gruppi, non vede le tue chat | ✅ Opzionale, subito |

La tabella nasce dal confronto iniziale con i bridge mautrix; con la scelta di Beeper
WhatsApp, Instagram e gli altri passano da Beeper, che usa gli stessi bridge.

**Su WeChat.** In Cina l'account WeChat è WeChat Pay, i contatti dell'università, i gruppi
del corso. Perderlo fino a gennaio costerebbe molto più di quanto valga vederlo
nell'Hub, quindi ogni soluzione va valutata prima di tutto sul rischio per l'account.
Da includere (decisione del 2026-09-25), strada da trovare. L'adattatore iLink ha
comunque un uso concreto subito: se la VPN si blocca, Hermes resta raggiungibile da
WeChat.

## 7. Sicurezza

- **Un solo ingresso pubblico**: l'URL ngrok, dietro token e cookie firmato. L'Hub ascolta
  solo su loopback e accetta modifiche solo dalla pagina dell'app stessa.
- **Il token è l'unica chiave**: chi lo ha, dalla fase 3 in poi, può leggere e scrivere
  file e dalla fase 5 leggere i messaggi. Va trattato come una password importante, e
  ruotato se finisce dove non deve.
- **Conferma esplicita** per tutto ciò che è distruttivo o esce verso altri: cancellare
  file, riavviare servizi, inviare messaggi. Audit log di ogni scrittura.
- **Allowlist nell'Hub**: cartelle, unit systemd, comandi. Mai fidarsi che il servizio a
  valle limiti da sé.
- **Da dire chiaramente**: con la fase 4 i bridge decifrano i messaggi sul VPS. La
  cifratura end-to-end di WhatsApp finisce lì, e chi compromette il VPS legge tutte le tue
  chat. È un compromesso da accettare consapevolmente, ed è il motivo per cui la sicurezza
  è la fase 0 e non l'ultima.

## 8. Rischi

| Rischio | Mitigazione |
|---|---|
| Un aggiornamento di Hermes cambia un endpoint | Versione fissata, contract test dopo ogni update, un modulo adattatore per ogni API esterna |
| WhatsApp o Meta cambiano protocollo e il bridge si rompe | mautrix si aggiorna in fretta; alert quando il bridge è disconnesso; aggiornamenti manuali, non automatici |
| Ban dell'account Instagram | Instagram dopo WhatsApp, stesso IP del telefono, nessun invio automatico senza approvazione |
| Prompt injection dai messaggi in arrivo | Profilo inbox senza strumenti pericolosi, approvazioni (fase 5) |
| Regressioni nella voce durante la riscrittura del frontend | Modulo voce portato così com'è, `?debug=1` come criterio di accettazione in ogni fase |
| Il progetto si allarga senza fine | Nella PWA solo ciò che usi dal telefono; il resto lo fa la dashboard |

## 9. Decisioni

Prese il 2026-09-25:

- **Accesso**: resta ngrok con token e cookie, nessun vincolo a Tailscale.
- **Inbox (fase 4)**: Beeper. Instagram passa di conseguenza dal bridge usato da Beeper.
- **WeChat**: da includere, con una strada da trovare che non rischi l'account.
- **Frontend**: riscritto con Vite + Preact nella fase 0.

- **Mirroring su Discord** delle sessioni vocali: tolto con la fase 1, come da
  piano. Le trascrizioni ora sono nell'app.

Ancora aperte:

- **Come integrare Beeper**, vedi fase 4.

## 10. Fase 0: completata il 2026-09-25

In produzione da `master`, stesso URL ngrok e stesso login di prima:

- Backend FastAPI in `/opt/hermes-hub` come utente `hermes-hub`, servizio
  `hermes-hub`, aggiornamenti con `deploy/deploy.sh`. Il vecchio `hermes-voice` è
  disabilitato ma ancora installato in `/root/hermes-voice` come ritorno indietro
  (`systemctl disable --now hermes-hub && systemctl enable --now hermes-voice`).
- L'Hub non aggiunge latenza: 2.86 s contro 2.99 s del vecchio server in misura
  alternata, e nel log di Hermes la richiesta arriva dall'Hub in pochi millisecondi.
- Hermes aggiornato da `3513a3b9` (v0.21.0) a `e726b798` (v0.21.5), contract check
  verde prima e dopo. L'aggiornamento ha aggiunto il toolset `connections` al canale API.
- Compressione del contesto spostata da Ollama al modello principale; Ollama rimosso.
- Memoria `memory_tencentdb` riparata in una sessione separata. Era rotta dal 23/09 e
  bloccava ogni turno per ~1.75 s mentre provava a ripartire.

**Dove va il tempo di un turno vocale**, misurato sul telefono prima delle correzioni
(11.7 s) e ricostruito dai log del VPS:

| Passaggio | Prima | Dopo | Note |
|---|---:|---:|---|
| Trascrizione (DeepInfra, diretta dal telefono) | 5.8 s | — | dal VPS 2.5-6.6 s su una frase di 4 s; il 6/09 era ~1.4 s |
| Attesa in Hermes prima del modello | 2.8 s | 0.01 s | dal secondo turno; il primo paga ~3.5 s di avvio della memoria |
| Modello | 1.2 s | 0.6-1.2 s | |
| Rete telefono ↔ server | ~0.9 s | ~0.9 s | due andate e ritorni via ngrok |
| Sintesi della prima frase | 0.65 s | 0.65 s | Edge, lato server |

Dopo le correzioni la prima frase arriva in 0.9-1.0 s dal momento in cui il testo parte
verso l'Hub; il tempo totale ora dipende soprattutto dalla trascrizione. Groq per la
trascrizione è stato valutato e scartato dall'utente.

Passa alla fase 1:

- **Il terminale dalla voce si blocca 60 s.** Il modello a volte usa il terminale anche
  per cose banali (un 12 × 3), le approvazioni sono `manual` con timeout 60 s e il canale
  vocale non ha modo di approvare: Hermes aspetta, blocca il comando e poi risponde. Lo
  risolve il foglio di approvazione sul telefono (fase 1, punto 4).
- Togliere il mirroring su Discord (decisione aperta, punto 9).

Pulizie rimaste, nessuna bloccante:

- `/root/hermes-voice` e il vecchio `hermes-voice.service`: da cancellare dopo qualche
  giorno senza bisogno di tornare indietro.
- Disco: 15 GB liberi. L'aggiornamento di Hermes ha occupato circa 3 GB (runtime Python
  gestito, strumenti browser), più di quanto abbia liberato Ollama.
- Regole firewall per 8081 e 3000 senza servizi in ascolto.
- Una trentina di sessioni di prova nella cronologia di Hermes (`ab-…`, `post-update-…`,
  `post-memfix-…`, `hub-*-test`).
- `~/.ssh/config` sul PC: il percorso della chiave è corretto e `ssh LORE-SERVER`
  funziona da Git Bash; l'OpenSSH di Windows rifiuta il file per un permesso rimasto a
  un utente Windows sconosciuto.

## 11. Fase 1: completata il 2026-09-25

Voce e testo sono la stessa conversazione, su una sessione di Hermes:

- **Endpoint misurato prima di migrare**: prima frase mediana 2.15 s sulle sessioni
  contro 2.18 s su chat completions, in misura alternata sul VPS. Quindi anche la
  voce è passata alle sessioni.
- **Criteri verificati** (sul VPS, copia di prova del branch accanto alla
  produzione):
  - latenza: prima frase mediana 1.51 s contro 1.53 s del percorso della fase 0,
    11 turni vocali alternati;
  - memoria: al 22° turno Hermes cita testualmente la prima frase della
    conversazione, che la finestra di 8 messaggi della fase 0 aveva perso;
  - approvazione: `echo $((12*3))` chiede conferma, approvato dal foglio sul
    telefono, risposta in pochi secondi invece dei 62 s della fase 0;
  - continuità voce/testo: stessa sessione, turni vocali con frasi e prompt
    vocale, turni scritti con Markdown.
- **I turni sopravvivono all'app che esce dallo schermo.** L'Hub legge lo stream
  di Hermes per conto suo e il telefono lo segue: staccato a metà risposta e
  ripreso 4 s dopo, il turno era finito `completed` con tutte le frasi. Prima,
  ogni disconnessione di iOS avrebbe interrotto Hermes. Anche il ricaricamento
  della pagina a metà turno è coperto.
- Elenco di tutte le conversazioni (app, Discord, terminale, cron) con ricerca,
  apertura e prosecuzione; stop del turno; audit log di approvazioni e stop.
- Risposte JSON compresse: una trascrizione da 82 KB passa a 24 KB, da 1.3 s a
  0.86 s dal PC in Cina. Gli stream restano non compressi.

Ritocco dopo il primo uso: la voce torna a schermo intero con l'animazione al
centro, aperta dal tasto accanto al campo di testo come in ChatGPT; chiudendola
si vede la chat. I blob seguono quattro bande di frequenza della voce (la tua
mentre ascolta, quella di Hermes mentre parla), mentre pensa non ascoltano niente
e si muovono in un altro modo; ogni stato ha i suoi colori. Provato nel browser
con un microfono simulato che pronunciava le domande: giro completo, livelli a 0
nelle pause di Hermes e mentre pensa.

Da provare sul telefono: la voce con il microfono vero (qui è stata verificata la
parte server, frasi comprese, non l'audio dell'iPhone).

Da sapere:

- "Approva anche quelli simili" vale fino alla fine di quella risposta, non per
  tutta la conversazione: su questo endpoint Hermes lega le approvazioni al turno.
  "Sempre" non è mai proposto.
- Se nessuno risponde, Hermes nega da solo il comando dopo `approvals.timeout`
  (60 s).
- Le sessioni di prova dei benchmark sono nella cronologia di Hermes insieme
  alle altre (vedi le pulizie al punto 10).

## 12. Fase 2: in produzione il 2026-09-25

- **Tab Server**: servizi sorvegliati (stato, da quanto, memoria, riavvii
  automatici), gli altri servizi in esecuzione ordinati per memoria, RAM,
  disco, carico, Hermes (versione, gateway, sessioni, piattaforme), costi di
  oggi e dei 7 giorni. Log di Hermes con filtro per livello e testo, journal dei
  servizi, aggiornamento automatico. Cron con pausa, ripresa e avvio (oggi non
  ce n'è nessuno).
- **Riavvii**: il piano diceva sudoers, ma l'Hub gira con `NoNewPrivileges` e
  `sudo` lì non funziona; polkit 0.105 di Ubuntu 22.04 non sa limitare a singole
  unit. Al suo posto un aiutante root attivato da socket
  (`deploy/control/`): risponde solo all'utente `hermes-hub` e tiene la sua
  lista (Hermes, dashboard, Web UI, ngrok, WARP). Verificato: `ssh` rifiutato,
  un altro utente non si collega, root respinto. Riavvio della Web UI dall'app
  riuscito e nel registro.
- **Avvisi push** senza servizi esterni oltre al push di Apple: cifratura RFC
  8291 e VAPID scritti su `cryptography`, identici byte per byte all'esempio
  della RFC. **Criterio verificato** con un servizio "canarino" temporaneo:
  fermato alle 11:09:18, avviso alle 11:11:4x (circa 2 min 25 s), avviso di
  ritorno alla ripartenza; canarino poi rimosso.
- Icona, manifest e worker delle notifiche ora sono pubblici: iOS li chiede
  senza cookie e i 401 riempivano il registro.

**Incidente durante la prova dei riavvii.** Il riavvio di prova della Web UI
(`hermes-webui`, nesquena) l'ha fatta entrare in un ciclo di ripartenze: dopo
l'aggiornamento di Hermes del mattino (v0.21.5) chi importa Hermes viene
rilanciato nel suo nuovo Python 3.14 gestito da `hermes pm`, dove manca
`pyyaml`, che la Web UI richiede. Girava ancora solo perché avviata il 23/09.
Fermata a mano dopo 88 ripartenze (circa un quarto di CPU). Poi **rimossa**
(decisione dell'utente, 25/09): nessuno la usava dal 17/06 (raggiungibile solo
via Tailscale: il firewall scarta la 8787 su `eth0`). Servizio disabilitato;
codice, sessioni e unit spostati in `/root/backups/hermes-webui-2026-09-25/`
(140 MB, cancellabili quando non servono più); tolta dalle liste dell'Hub e
dell'aiutante root. Ha anche mostrato un difetto del sorvegliante,
corretto: un ciclo di ripartenze ora dà un solo avviso "continua a ripartire"
(3 riavvii in 10 minuti) e uno quando si stabilizza, e i riavvii sporadici al
massimo una notifica ogni 30 minuti.

Da fare con il telefono: attivare "Avvisi sul telefono" dall'app sulla
schermata Home e mandare la notifica di prova (la consegna tramite Apple non si
può provare dal PC).

Notato durante la fase 2: `nume-web` usa 1,4 GB di RAM e `nerve` (vecchia UI di
OpenClaw, non più in uso) è ancora attivo.

## Riepilogo

| Fase | Cosa ottieni | Stima |
|---|---|---|
| 0 | Backend solido, scheletro della nuova UI, Hermes aggiornato — **fatta** | 1 g |
| 1 | Chat voce + testo con trascrizioni, sessioni di tutti i canali, approvazioni — **fatta** | 1 g |
| 2 | Stato del server, log, cron, costi, notifiche push — **fatta** | 1 g |
| 3 | Vault Obsidian e file dal telefono | 3-4 gg |
| 4 | Chat da Beeper (WhatsApp, Instagram, ...), poi WeChat | da stimare |
| 5 | Hermes che legge, riassume e risponde ai messaggi, con la tua conferma | 5-7 gg |
