import sys
import socket
import time
import re

sys.stdout.reconfigure(encoding='utf-8')  # Pour éviter l'erreur charmap sur Windows

class GrandMA2Telnet:
    def __init__(self, host="127.0.0.1", port=30000, timeout=2, user="Administrator", password=None):
        """ Initialise la connexion socket à GrandMA2 OnPC """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.user = user
        self.password = password
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
        
    def list_executor(self):
        self.executorList = self.send_command("List Executor")
        
        
        lines = self.executorList.splitlines() 
        nbLines = 0
        for line in lines:
            try:
                currentPage, execId = line.split()[1].split(".")
                #print(line)
                
                execName = line.split()[3]
                execName = execName.removeprefix("Name")
                print ("Page " + currentPage + " | ExecId:" + execId + ":" + execName)
                self.execIdToName[int(execId)] = execName
                nbLines+=1
            except:
                continue
                
        while nbLines < 10: #fill when no exec are set
            self.execIdToName[nbLines] = ""
            nbLines +=1
                        
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



    def updateFaderLabels(self, console):
        self.list_executor()
        for i in range(8):
            label = self.execIdToName.get(i+1)
            print(str(i) + " : " +label)
            #label = ''.join(c for c in label if c.isprintable())
            label = re.sub("[^a-z0-9-]+","", label, flags=re.IGNORECASE)
            label = label.removeprefix("33mName37m")
            label = label.ljust(7)
            print("LABEL : " + label)
            console.sendXtouchScribble(i, label)
            
        for i in range(100,108):
            label = self.execIdToName.get(i+1)
            print(str(i) + " : " +label)
            label = re.sub("[^a-z0-9-]+","", label, flags=re.IGNORECASE)
            label = label.removeprefix("33mName37m")
            label = label.ljust(7)
            print("LABEL : " + label)
            console.sendXtouchScribbleRaw2(i-100, label)
