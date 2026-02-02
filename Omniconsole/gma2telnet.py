import sys
import socket
import time
import re

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
        self.executorList = ""
        self.execIdToName = {}
        
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
            
            self.send_command(login_command)
            
    


        except Exception as e:
            print(f"❌ Erreur de connexion : {e}")
        
    def _extract_exec_name(self, line):
        clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        name_match = re.search(r'Name\s*"?([^"\r\n]*)"?', clean_line)
        if name_match:
            return name_match.group(1).strip()
        colon_match = re.search(r":\s*=?\s*(.*)", clean_line)
        if colon_match:
            tail = colon_match.group(1)
            parts = re.split(r"\s+[A-Za-z]+=", tail, maxsplit=1)
            return parts[0].strip()
        return clean_line.strip()

    def list_executor(self):
        self.executorList = self.send_command("List Executor")
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
            f"List Executor {page}.{start_exec} Thru {page}.{end_exec}"
        )
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
                    print(
                        "Page " + str(currentPage) + " | ExecId:" + str(execId) + ":" + execName
                    )
                self.execIdToName[(currentPage, execId)] = execName
            except:
                continue
                        
    def send_command(self, command):
        """ Envoie une commande Telnet à GrandMA2 """
        print("Sending telnet command")
        if self.socket:
            try:
                command_str = command + "\r"
                self.socket.sendall(command_str.encode('utf-8'))
                time.sleep(0.01)  # Pause pour assurer la réception
                
                response = self.socket.recv(32096).decode('utf-8', errors='ignore')
                print(f"📤 Commande envoyée : {command}")
                if response:
                    #print(f"📥 Réponse : {response}")
                    if("Error" in response):
                        print("GMA2 ERROR : " + response)
                    else:
                        return response
            except Exception as e:
                print(f"❌ Erreur lors de l'envoi de la commande : {e}")
        else:
            print("⚠️ Aucune connexion active à GrandMA2 !")

    def close(self):
        """ Ferme la connexion proprement """
        if self.socket:
            self.socket.close()
            print("🔌 Connexion fermée.")



    def updateFaderLabels(self, console, page=1, include_buttons=False):
        self.list_executor()
        for i in range(8):
            label = self.execIdToName.get((page, i + 1), "")
            #label = ''.join(c for c in label if c.isprintable())
            label = re.sub(r"\x1b\[[0-9;]*m", "", label)
            label = re.sub("[^a-z0-9- ]+","", label, flags=re.IGNORECASE)
            label = label.removeprefix("33mName37m")
            label = label.ljust(7)
            console.sendXtouchScribble(i, label)
        
        if include_buttons:
            for i in range(100,108):
                label = self.execIdToName.get((page, i + 1), "")
                label = re.sub(r"\x1b\[[0-9;]*m", "", label)
                label = re.sub("[^a-z0-9- ]+","", label, flags=re.IGNORECASE)
                label = label.removeprefix("33mName37m")
                label = label.ljust(7)
                console.sendXtouchScribbleRaw2(i-100, label)

    def updateButtonLabels(self, console, page=1):
        self.list_executor_range(page, 101, 108)
        if not self.execIdToName:
            self.list_executor()
        for i in range(100,108):
            label = self.execIdToName.get((page, i + 1), "")
            label = re.sub(r"\x1b\[[0-9;]*m", "", label)
            label = re.sub("[^a-z0-9- ]+","", label, flags=re.IGNORECASE)
            label = label.removeprefix("33mName37m")
            label = label.ljust(7)
            console.sendXtouchScribbleRaw2(i-100, label)
