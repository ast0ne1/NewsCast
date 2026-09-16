# Install NewsCast on a Raspberry Pi

This guide is for a **new Raspberry Pi** that already has Raspberry Pi OS installed and is **on the same Wi‑Fi or network** as your phone or computer.

You will use your Windows PC to prepare a small folder, copy it to the Pi, then run one command on the Pi.

You can do that with a **USB stick and a screen on the Pi**, or **entirely from your Windows PC** (see [Connect from your Windows PC](#connect-from-your-windows-pc)).

You need:

- The Windows PC that has this NewsCast project
- The Pi on the **same Wi‑Fi or network** as that PC
- Either a USB stick and a screen on the Pi, **or** remote access (SSH) already on

---

## 1. Prepare the folder on Windows

1. Open the NewsCast project folder on your PC.
2. Open the `deploy` folder.
3. Double-click **`export-pi.bat`**.
4. A black window will appear. When it says **Ready to copy to the Pi**, you can close it.

The folder to copy is:

`NewsCast` → `dist` → **`NewsCast-pi`**

That folder is small. It does **not** include your Windows login, tests, or the large `.venv` folder.

---

## 2. Copy it onto a USB stick

1. Plug the USB stick into the PC.
2. Copy the whole **`NewsCast-pi`** folder onto the stick (not just the files inside it).
3. Eject the stick safely, then unplug it.

---

## 3. Copy it onto the Pi

1. Plug the USB stick into the Pi.
2. On the Pi desktop, open **File Manager** (the folder icon).
3. Open the USB stick, then copy **`NewsCast-pi`** to the **Desktop**.
4. You can unplug the USB stick when the copy has finished.

Skip steps 2–3 if you copied the folder over the network instead (see [Copy the folder from Windows](#copy-the-folder-from-windows)).

---

## 4. Install NewsCast

1. Open **Terminal** on the Pi (Raspberry menu → **Accessories** → **Terminal**), or an SSH window from your PC.
2. Type this line and press Enter (it moves you into the folder on the Desktop):

```bash
cd ~/Desktop/NewsCast-pi
```

3. Type this line and press Enter:

```bash
sudo bash deploy/install.sh --hostname newscast
```

4. If it asks for a password, type the password you set when you installed Raspberry Pi OS. Nothing will appear as you type. That is normal. Press Enter.

The install can take several minutes the first time. Leave the window open until it prints that NewsCast is installed and running.

`--hostname newscast` names the Pi **newscast** so you can open it as a web address instead of an IP number. You can pick another short name (letters, numbers, hyphens only), for example `--hostname living-room`.

---

## 5. Open NewsCast

On your phone or computer (same Wi‑Fi as the Pi), open a browser and go to:

**http://newscast.local:8080**

Sign in with:

- Username: **admin**
- Password: **admin**

Then go to **Settings** and change that password.

If `newscast.local` does not load, wait a minute and try again. If it still fails, on the Pi Terminal type:

```bash
hostname -I
```

Use the first number it prints, for example `http://192.168.1.20:8080`.

### Optional LAN HTTPS

HTTPS is **off by default**. Turn it on under **Settings → General → Use HTTPS on the LAN**. NewsCast creates a household certificate and serves HTTPS on the **same port** (usually 8080). The app restarts after you save.

1. Set a **Hostname** first if you want `https://newscast.local:8080` (recommended).
2. Enable **Use HTTPS on the LAN** and save. Wait for the restart.
3. Open `https://newscast.local:8080` (or `https://<pi-ip>:8080`).
4. Download the **root CA** from Settings → General and trust it once on each phone or PC:

   - **Windows:** double-click the `.pem`, Install Certificate → Local Machine → Trusted Root Certification Authorities.
   - **iPhone:** AirDrop or download the file in Safari → Settings → General → VPN & Device Management → install the profile → Settings → General → About → Certificate Trust Settings → enable full trust.
   - **Android:** Settings → Security → Encryption & credentials → Install a certificate → CA certificate (wording varies by manufacturer).

Until HTTPS is on, passwords travel in cleartext on the Wi‑Fi. Password hashing at rest is always on. Treat NewsCast as a **trusted home-network** service (prefer the home SSID; avoid exposing the port to the internet). Xteink Sync stays reachable on the LAN without a separate login — that is intentional so Sync on the reader keeps working.


### Household accounts (optional)

On **Settings → Users**, the admin can add other people. Each person gets their own feeds, daily paper, Send files, and OPDS catalog.

- Approve which Catalog sources they may Add under **Settings → Catalog approvals**.
- Turn on custom feed URLs and/or ntfy alerts on each person’s card if you want those.
- On **Status**, each signed-in user should copy **their** catalog URL (`/opds/u/<username>`) into CrossPoint or KOReader — not the shared `/opds` admin catalog.
- A one-time **login QR** on the Users card signs someone in without typing the password on a phone.

---

## Connect from your Windows PC

Use this if the Pi has **no screen**, or you would rather work from your desk. Your PC and the Pi must be on the **same Wi‑Fi**.

The Pi’s name is usually **`raspberrypi`** until NewsCast install renames it to **`newscast`**. The username and password are the ones you chose in Raspberry Pi Imager (not `admin` / `admin` — that is only for the NewsCast website).

### Turn on remote access (SSH)

SSH lets you type commands on the Pi from Windows.

**If you still have a screen on the Pi for a few minutes:**

1. Click the Raspberry menu → **Preferences** → **Raspberry Pi Configuration**.
2. Open the **Interfaces** tab.
3. Switch **SSH** to **Enabled**.
4. Click **OK**.

**If you are still writing the SD card on your PC:** in Raspberry Pi Imager, click the gear (or **Edit settings**), turn on **Enable SSH**, and set a username and password before you write the card.

### Open a command window on the Pi from Windows

1. On the PC, press the Windows key, type **PowerShell**, and open it.
2. Type this and press Enter. Change `pi` if your Pi username is different:

```powershell
ssh pi@raspberrypi.local
```

3. The first time, it may ask if you trust the host. Type **`yes`** and press Enter.
4. Type your **Pi user password**. Nothing appears as you type. Press Enter.

You should see a prompt that looks like `pi@raspberrypi`. You are now typing on the Pi. To leave later, type `exit` and press Enter.

If `raspberrypi.local` fails, find the Pi’s address in your router’s device list, then use `ssh pi@192.168.1.20` (use the real number).

### See the Pi desktop in a browser (optional)

Raspberry Pi OS with a desktop can use **Raspberry Pi Connect**:

1. On the Pi (once, with a screen or after SSH), open **Raspberry Pi Connect** from the menu, sign in with a Raspberry Pi ID, and turn it on.
2. On your PC, open [connect.raspberrypi.com](https://connect.raspberrypi.com) and start a **Screen sharing** session.

That gives you the Pi desktop in the browser. You can then use File Manager and Terminal as in the USB steps.

### Copy the folder from Windows

After [step 1](#1-prepare-the-folder-on-windows), you can skip the USB stick.

**Easier (a window of files):**

1. On the PC, install [WinSCP](https://winscp.net/) and open it.
2. File protocol: **SFTP**. Host name: `raspberrypi.local`. Port: **22**. User name and password: your Pi login.
3. Click **Login**.
4. On the left, open `NewsCast` → `dist`. On the right, open **Desktop** (or your home folder).
5. Drag **`NewsCast-pi`** from the left to the right.

**Or from PowerShell** (after `export-pi.bat`), from the NewsCast project folder:

```powershell
scp -r dist\NewsCast-pi pi@raspberrypi.local:Desktop/
```

Then in SSH, the install folder is still `~/Desktop/NewsCast-pi`.

---

## After install

- **Feeds** and **Catalog** add news sources. Non-admins only see Catalog entries the admin approved.
- **Refresh** in the header pulls stories.
- **Settings** is split into tabs: General (hostname, instance name, HTTPS, interface language), Publication, Schedule, Filters, Translation, LLM (OpenAI or Ollama), Reader (CrossPoint or Kobo push), Notifications (ntfy), Categories, Catalog approvals, Users, Backup/Restore, Update (GitHub Releases), and About. Non-admins only see the tabs that apply to them.
- **Status** shows your personal OPDS URL (`/opds/u/<username>`) for the reader catalog.
- **Search** finds stories, favourites, and Saved long-reads. Briefing has Today and Yesterday. Feeds can mute a source for 24 hours.
- NewsCast starts by itself when the Pi is turned on.

To see if it is running, in Terminal on the Pi:

```bash
sudo systemctl status newscast
```

Press **q** to leave that screen.

---

## Updating later

On Windows, run **`export-pi.bat`** again and copy the new `NewsCast-pi` folder onto the Pi (replace the old copy on the Desktop). Then in Terminal:

```bash
cd ~/Desktop/NewsCast-pi
sudo bash deploy/install.sh
```

Your stories, password, and settings stay on the Pi. You do not need `--hostname` again unless you want to rename it.

After the project is on GitHub with a Release, you can also update from **Settings → Update**: set the repository (`owner/NewsCast`), tap **Check for updates**, then **Install**. That backs up your data first and does not overwrite `.env` or the `data` folder. **Download backup** and **Roll back last app** are on the Backup/Restore tab.

---

## If something goes wrong

| What you see | What to try |
| --- | --- |
| `No such file or directory` after `cd` | The folder is not on the Desktop, or it has a different name. In File Manager, put `NewsCast-pi` on the Desktop and try again. |
| `install.sh: not found` | You are not inside `NewsCast-pi`. Run the `cd` line first. |
| Browser cannot open `newscast.local` | Phone/PC must be on the same Wi‑Fi. Or use the IP from `hostname -I`. |
| `ssh` says connection refused | SSH is off. Enable it in Raspberry Pi Configuration → Interfaces, or in Raspberry Pi Imager before writing the card. |
| `ssh` cannot find `raspberrypi.local` | Same Wi‑Fi as the Pi. Or use the IP from your router instead of the name. |
| Sign-in does not work | Use `admin` / `admin` unless you already changed it in Settings. |
| Want a different name | On the Pi: `sudo /opt/newscast/deploy/set-hostname.sh living-room` or change **Hostname** on the Settings page. |
