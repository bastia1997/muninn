<p align="center"><img src="assets/png/muninn_128.png" width="96" alt=""></p>

<p align="center"><a href="README.md">English</a> · <b>Italiano</b></p>

# Muninn

*Hears all. Remembers all.* — Il nome viene da Muninn, il corvo della memoria di Odino: vola per il mondo, ascolta tutto e
torna a riferire.

Registra microfono, audio della call, schermi e finestre (es. Teams). Quando fermi la registrazione trascrive,
riconosce chi parla e salva una cartella pronta da dare a un'IA. In più: sottotitoli live a schermo e la finestra
**Notaio** con tutto quello che è stato detto.

Usa solo **DeepInfra** con Nvidia **Nemotron 3.5**: trascrizione con i tempi di ogni parola e separazione delle voci
della call. I nomi li assegni tu ascoltando qualche spezzone: nessun'altra IA coinvolta. Windows 10/11.

## Installazione

Serve Python 3.10+ con tkinter (l'installer standard di python.org).

1. Scarica il codice: **Code → Download ZIP** ed estrailo, oppure `git clone https://github.com/bastia1997/muninn.git`.
2. Avvia **`install.bat`**: crea `.venv` con le dipendenze (ffmpeg compreso, tramite `imageio-ffmpeg`) e un collegamento
   «Muninn» sul Desktop. Se Python manca prova a installarlo con winget.
3. Avvia Muninn con **`run.bat`** o con il collegamento sul Desktop.
4. Apri **Impostazioni**: incolla la chiave DeepInfra (https://deepinfra.com/dash/api_keys), scrivi il tuo nome,
   **Salva impostazioni**.

Per aggiornare scarica la nuova versione (o `git pull`) e rilancia `install.bat`. Per disinstallare cancella la cartella
e il collegamento sul Desktop.

**Dove sono i tuoi dati**: `config.json` (impostazioni e chiave DeepInfra, in chiaro), `recordings\` ed `error.log`
restano nella cartella del progetto.

### Installatore autonomo (facoltativo)

`tools\build.bat` crea `dist\Muninn-Setup.exe` e `dist\Muninn-Uninstall.exe`: un installatore che non richiede Python
né diritti di amministratore. Installa in `%LOCALAPPDATA%\Programs\Muninn` con collegamenti su Desktop e menu Start, e
compare in «App installate» di Windows.

| Cosa | Dove (versione installata) |
|---|---|
| Impostazioni e chiave DeepInfra (in chiaro) | `%APPDATA%\Muninn\config.json` |
| Registrazioni e Notaio | `Documenti\Muninn` (si cambia in Impostazioni) |
| Errori all'avvio | `%APPDATA%\Muninn\error.log` |

Gli exe non sono firmati: SmartScreen di Windows o alcuni antivirus possono segnalarli o bloccarli.

## Uso

1. **Audio**: microfono → etichetta "Me"; uscita cuffie/casse → etichetta "Call" con *Più persone (call)* spuntato.
   Non sai qual è il dispositivo giusto? **Prova** (sul microfono registra e fa riascoltare, sull'uscita suona un
   din-don) oppure **Mostra livelli** e guarda quale barra si muove.
2. **Video**: spunta lo schermo dove c'è Teams (o aggiungi la finestra): a destra vedi l'**anteprima** dal vivo.
3. **Avvia registrazione** … **Ferma e salva**. Il resto lo fa da solo.

**File già registrati** (registrazioni di Teams, MP4, MP3, M4A…): **Elabora file…**, scegline uno o più. Per ognuno
viene creata una cartella come per le registrazioni.

**Sottotitoli live**: sottotitoli a schermo (di solito 1-2 s di ritardo) e finestra **Notaio**, invisibili in
condivisione schermo. Usano le sorgenti spuntate in *Audio*, funzionano anche senza registrare, costano circa 1-3
centesimi l'ora. **Real time EN**: streaming vero (circa mezzo secondo), **solo per l'inglese** (circa 0,06 $ l'ora per
sorgente): DeepInfra offre lo streaming vero solo per il modello inglese.

**Tray**: Muninn non occupa la barra delle applicazioni, vive nell'icona vicino all'orologio. La X nasconde solo la
finestra (registrazione e sottotitoli continuano); tasto destro sull'icona per **Esci**.

**Lingua**: Impostazioni → Lingua (italiano, English, français, Deutsch, español).

La scheda **Guida** del programma spiega tutto passo per passo.

## Cosa trovi nella cartella della registrazione

| File | Contenuto |
|---|---|
| `video_<nome>.mp4` | Video con audio e **due tracce di sottotitoli dentro**: *Transcript* (con il nome di chi parla, predefinita) e *Transcript (no names)* (in VLC: menu Sottotitoli) |
| `transcript.md` | Conversazione in ordine, con orari e nomi: **è quella da dare all'IA** |
| `transcript.json` | Stessa cosa in formato strutturato (secondi, orario reale, parlante, testo) |
| `speakers.json` | Chi è chi: Speaker N → nome |
| `data\` | Materiale tecnico: tracce audio, `.srt` (con e senza nomi), risposte delle API, `session.json` |

## Come si assegnano i nomi

1. Il microfono è trascritto a parte ed è sempre tuo (compare col nome messo in Impostazioni).
2. L'audio della call va a Nemotron con la separazione delle voci: ogni voce diventa *Speaker 1, 2…*. Fino a
   1 ora va in una sola richiesta. Oltre l'ora è diviso in blocchi e il programma ricollega le voci della stessa
   persona tra un blocco e l'altro con una richiesta in più (mai due voci dello stesso blocco).
3. Si apre la finestra **Interlocutori**: per ogni voce i pulsanti ▶ fanno sentire 5 secondi in cui parla solo lei.
   Scrivi o scegli il nome e premi **Applica i nomi**: vengono sostituiti in trascrizione, sottotitoli e video.
4. Stessa persona → stesso nome; se non lo sai lascia vuoto. **Più tardi** chiude senza cambiare nulla (si riapre
   dalla scheda *Trascrizione*).

## Cose da sapere

- Usa le **cuffie**: altrimenti il microfono riprende anche gli altri e il testo si duplica.
- *Area dello schermo* registra la zona della finestra all'avvio; *Finestra diretta* la segue ma con Teams e i browser
  può dare video nero (l'anteprima te lo mostra). Lo schermo intero è la scelta più sicura.
- **Privacy**: l'audio viene inviato a DeepInfra (dichiara di non salvarlo su disco né usarlo per addestrare). Per
  registrazioni di lavoro verifica che sia consentito e informa i partecipanti.

## Per chi sviluppa

```bat
install.bat        :: crea .venv con le dipendenze e un collegamento sul Desktop ai sorgenti
run.bat            :: avvia dai sorgenti (config.json e recordings\ restano nella cartella del progetto)
tools\build.bat    :: crea dist\Muninn-Setup.exe e dist\Muninn-Uninstall.exe (PyInstaller)
```

- Codice in `muninn\`, installatore e disinstallatore in `tools\installer\` (solo libreria standard).
- Versione: `__version__` in `muninn\__init__.py` (finisce in «App installate»).
- Codice, commenti e testi sorgente sono in inglese. Traduzioni: `muninn\lang\<lingua>.json` (chiave = testo inglese
  passato a `_()` nel codice) e `muninn\lang\guide_<lingua>.txt`; la Guida in inglese è in `muninn\guide.py`.
- Icone: SVG in `assets\svg\`; PNG e `muninn.ico` si rigenerano con `tools\make_icons.py` (usa Chrome headless).
- Gli exe non vanno nel repository (`dist\` è in `.gitignore`).
- `config.json` contiene la chiave in chiaro ed è in `.gitignore`: non toglierlo da lì.

**Installazioni automatiche**: `Muninn-Setup.exe --quiet [--dir <cartella>] [--no-shortcuts] [--no-register]` e
`Muninn-Uninstall.exe --quiet [--delete-settings]`.

## Licenza

[MIT](LICENSE). Muninn usa ffmpeg (tramite `imageio-ffmpeg`), distribuito con la sua licenza GPL/LGPL.
