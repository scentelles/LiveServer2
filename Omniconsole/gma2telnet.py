import sys
import select
import socket
import time
import re
import threading

sys.stdout.reconfigure(encoding='utf-8')  # Pour éviter l'erreur charmap sur Windows

class GrandMA2Telnet:
    def __init__(self, host="127.0.0.1", port=30000, timeout=2, user="Administrator", password=None, verbose=False):
        """ Initialise la connexion socket à GrandMA2 OnPC """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.user = user
        self.password = password
        self.verbose = verbose
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 1.0
        self.executorList = ""
        self.execIdToName = {}
        self._socket_lock = threading.Lock()
        self._stop_drain = threading.Event()
        self._drain_thread = None
        
    def connect(self):
        """ Établit la connexion au serveur Telnet et se connecte en tant qu'administrateur """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.host, self.port))
            print(f"✅ Connecté à GrandMA2 OnPC ({self.host}:{self.port})")

            # 🔑 Se connecter avec un utilisateur spécifique
            if self.password:
                login_command = f'Login {self.user} "{self.password}"'
            else:
                login_command = f'Login {self.user}'
            
            # Start drain thread before sending first command
            self._stop_drain.clear()
            self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
            self._drain_thread.start()
            
            self.send_command(login_command, wait_for_response=True)
            
    


        except Exception as e:
            print(f"❌ Erreur de connexion : {e}")
            return False
        return True

    def _reconnect(self):
        """ Tente de se reconnecter au serveur GrandMA2 avec backoff """
        self._stop_drain.set()
        
        for attempt in range(1, self._max_reconnect_attempts + 1):
            delay = self._reconnect_delay * attempt
            print(f"🔄 Tentative de reconnexion {attempt}/{self._max_reconnect_attempts} dans {delay:.1f}s...")
            time.sleep(delay)
            try:
                if self.socket:
                    try:
                        self.socket.close()
                    except Exception:
                        pass
                    self.socket = None
                if self.connect():
                    print(f"✅ Reconnexion réussie (tentative {attempt})")
                    return True
            except Exception as e:
                print(f"❌ Échec reconnexion tentative {attempt}: {e}")
        print("❌ Reconnexion impossible après toutes les tentatives.")
        return False
        
    def _drain_loop(self):
        """ Vide en permanence le buffer de réception en arrière-plan pour éviter que la GMA2 ne freeze. """
        while not self._stop_drain.is_set():
            if self.socket:
                try:
                    with self._socket_lock:
                        r, _, _ = select.select([self.socket], [], [], 0.0)
                        if r:
                            self.socket.recv(32096)
                except Exception:
                    pass
            time.sleep(0.05)

    def _extract_exec_name(self, line):
        clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        clean_line = clean_line.strip()
        def _trim_meta(name):
            name = name.strip()
            if not name:
                return ""
            name = re.split(r"\s+Seq\b", name, maxsplit=1)[0]
            name = re.split(r"\s+[A-Za-z0-9_]+=", name, maxsplit=1)[0]
            return name.strip()
        def _sanitize(name):
            name = _trim_meta(name)
            if not name:
                return ""
            lowered = name.lower()
            if lowered.startswith("list executor"):
                return ""
            return name

        if clean_line.lower().startswith("list executor"):
            return ""
        name_match = re.search(r'Name\s*"?([^"\r\n]*)"?', clean_line)
        if name_match:
            name = name_match.group(1).strip()
            name = _trim_meta(name)
            return _sanitize(name)
        colon_match = re.search(r":\s*=?\s*(.*)", clean_line)
        if colon_match:
            tail = colon_match.group(1)
            parts = re.split(r"\s+[A-Za-z0-9_]+=", tail, maxsplit=1)
            name = parts[0].strip()
            name = _trim_meta(name)
            return _sanitize(name)
        name = clean_line.strip()
        name = _trim_meta(name)
        return _sanitize(name)

    def list_executor(self):
        self.executorList = self.send_command("List Executor", wait_for_response=True)
        self.execIdToName = {}
        if not self.executorList:
            return
        
        lines = self.executorList.splitlines() 
        for line in lines:
            try:
                match = re.search(r"(\d+)\.(\d+)", line)
                if not match:
                    continue
                currentPage = int(match.group(1))
                execId = int(match.group(2))
                execName = self._extract_exec_name(line)
                execName = execName.removeprefix("Name").removeprefix("=")
                if self.verbose:
                    print ("Page " + str(currentPage) + " | ExecId:" + str(execId) + ":" + execName)
                self.execIdToName[(currentPage, execId)] = execName
            except:
                continue

    def list_executor_range(self, page, start_exec, end_exec):
        self.executorList = self.send_command(
            f"List Executor {page}.{start_exec} Thru {page}.{end_exec}",
            wait_for_response=True
        )
        time.sleep(0.4)
        if not self.executorList:
            return

        lines = self.executorList.splitlines()
        for line in lines:
            try:
                match = re.search(r"(\d+)\.(\d+)", line)
                if not match:
                    continue
                currentPage = int(match.group(1))
                execId = int(match.group(2))
                execName = self._extract_exec_name(line)
                execName = execName.removeprefix("Name").removeprefix("=")
                if self.verbose:
                    print(
                        "Page " + str(currentPage) + " | ExecId:" + str(execId) + ":" + execName
                    )
                self.execIdToName[(currentPage, execId)] = execName
            except:
                continue
                        
    def send_command(self, command, wait_for_response=False):
        """ Envoie une commande Telnet à GrandMA2, avec reconnexion automatique """
        if not self.socket:
            print("⚠️ Aucune connexion active à GrandMA2, tentative de reconnexion...")
            if not self._reconnect():
                return None
        try:
            return self._send_command_inner(command, wait_for_response)
        except (ConnectionError, OSError, socket.error) as e:
            print(f"❌ Connexion perdue ({e}), reconnexion...")
            if self._reconnect():
                try:
                    return self._send_command_inner(command, wait_for_response)
                except Exception as e2:
                    print(f"❌ Échec après reconnexion : {e2}")
            return None
        except Exception as e:
            print(f"❌ Erreur lors de l'envoi de la commande : {e}")
            return None

    def _send_command_inner(self, command, wait_for_response=False):
        """ Envoie effectivement la commande (sans logique de reconnexion) """
        if self.verbose:
            print(f"📤 Commande envoyée : {command}")
            
        with self._socket_lock:
            if wait_for_response:
                # Vider le buffer de reception (drain)
                while True:
                    r, _, _ = select.select([self.socket], [], [], 0.0)
                    if r:
                        try:
                            self.socket.recv(32096)
                        except Exception:
                            break
                    else:
                        break

            command_str = command + "\r\n"
            self.socket.sendall(command_str.encode('utf-8'))
            
            if not wait_for_response:
                return None
                
            time.sleep(0.01)  # Pause pour assurer la reception
        
            chunks = []
            timeout = 0.2
            while True:
                r, _, _ = select.select([self.socket], [], [], timeout)
                if not r:
                    break
                try:
                    data = self.socket.recv(32096)
                except socket.timeout:
                    break
                except Exception:
                    break
                if not data:
                    break
                chunks.append(data.decode('utf-8', errors='ignore'))
                if len(data) < 32096:
                    break
                timeout = 0.05
            response = "".join(chunks)
            if response:
                if("Error" in response):
                    print("GMA2 ERROR : " + response)
                else:
                    return response
        return None

    def close(self):
        """ Ferme la connexion proprement """
        self._stop_drain.set()
        if self._drain_thread:
            self._drain_thread.join(timeout=0.2)
        if self.socket:
            self.socket.close()
            print("🔌 Connexion fermée.")


    def fetch_all_labels(self):
        """ Récupère tous les labels des 4 pages au démarrage pour éviter de le faire à la volée. """
        print("⏳ Récupération des noms d'exécuteurs...")
        self.execIdToName = {}
        for p in range(1, 5):
            self.list_executor_range(p, 1, 8)
            self.list_executor_range(p, 101, 108)
        print("✅ Noms d'exécuteurs chargés.")

    def updateFaderLabels(self, console, page=1, include_buttons=False):
        for i in range(8):
            label = self.execIdToName.get((page, i + 1), "")
            #label = ''.join(c for c in label if c.isprintable())
            label = re.sub(r"\x1b\[[0-9;]*m", "", label)
            label = re.sub("[^a-z0-9- ]+","", label, flags=re.IGNORECASE)
            label = label.removeprefix("33mName37m")
            label = label[:7]
            if len(label) < 7:
                pad = 7 - len(label)
                left = pad // 2
                right = pad - left
                label = (" " * left) + label + (" " * right)
            console.sendXtouchScribble(i, label)
        
        if include_buttons:
            for i in range(100,108):
                label = self.execIdToName.get((page, i + 1), "")
                label = re.sub(r"\x1b\[[0-9;]*m", "", label)
                label = re.sub("[^a-z0-9- ]+","", label, flags=re.IGNORECASE)
                label = label.removeprefix("33mName37m")
                label = label[:7]
                if len(label) < 7:
                    pad = 7 - len(label)
                    left = pad // 2
                    right = pad - left
                    label = (" " * left) + label + (" " * right)
                console.sendXtouchScribbleRaw2(i-100, label)

    def updateButtonLabels(self, console, page=1):
        for i in range(100,108):
            label = self.execIdToName.get((page, i + 1), "")
            label = re.sub(r"\x1b\[[0-9;]*m", "", label)
            label = re.sub("[^a-z0-9- ]+","", label, flags=re.IGNORECASE)
            label = label.removeprefix("33mName37m")
            label = label[:7]
            if len(label) < 7:
                pad = 7 - len(label)
                left = pad // 2
                right = pad - left
                label = (" " * left) + label + (" " * right)
            console.sendXtouchScribbleRaw2(i-100, label)
