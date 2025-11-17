import socket
import os
import struct
import json
import threading
import hashlib
import time
from pathlib import Path

# Configuration
NODE_PORT = 9999
DISCOVERY_PORT = 8888
SHARED_FOLDER = './shared_files'
PEERS_FILE = './peers.json'
BUFFER_SIZE = 65536
DISCOVERY_INTERVAL = 10  # Secondes

os.makedirs(SHARED_FOLDER, exist_ok=True)


class P2PNode:
    """Nœud P2P avec découverte automatique des pairs"""

    def __init__(self, node_id, port=NODE_PORT):
        self.node_id = node_id
        self.port = port
        self.peers = {}  # {peer_id: {'ip': ip, 'port': port, 'last_seen': timestamp}}
        self.shared_files = {}
        self.local_ip = self.get_local_ip()
        self.load_peers()
        self.index_files()
        print(f"🆔 Node ID : {self.node_id}")
        print(f"🌐 IP locale : {self.local_ip}")
        print(f"🔌 Port : {self.port}\n")

    def get_local_ip(self):
        """Récupère l'IP locale du réseau"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
        except:
            ip = '127.0.0.1'
        return ip

    def get_network_range(self):
        """Retourne la plage réseau à scanner"""
        parts = self.local_ip.split('.')
        return f"{parts[0]}.{parts[1]}.{parts[2]}"

    def get_file_hash(self, filepath):
        """Calcule le hash SHA-256 d'un fichier"""
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    def index_files(self):
        """Index tous les fichiers du dossier partagé"""
        self.shared_files = {}
        for filename in os.listdir(SHARED_FOLDER):
            filepath = os.path.join(SHARED_FOLDER, filename)
            if os.path.isfile(filepath):
                file_hash = self.get_file_hash(filepath)
                self.shared_files[filename] = file_hash

    def save_peers(self):
        """Sauvegarde les pairs connus"""
        with open(PEERS_FILE, 'w') as f:
            peers_to_save = {
                k: {'ip': v['ip'], 'port': v['port']}
                for k, v in self.peers.items()
            }
            json.dump(peers_to_save, f)

    def load_peers(self):
        """Charge les pairs depuis le fichier"""
        if os.path.exists(PEERS_FILE):
            with open(PEERS_FILE, 'r') as f:
                saved_peers = json.load(f)
                for peer_id, info in saved_peers.items():
                    self.peers[peer_id] = {
                        'ip': info['ip'],
                        'port': info['port'],
                        'last_seen': time.time()
                    }

    def add_peer(self, peer_id, ip, port):
        """Ajoute un pair au réseau"""
        if peer_id != self.node_id:  # Ne pas ajouter soi-même
            self.peers[peer_id] = {
                'ip': ip,
                'port': port,
                'last_seen': time.time()
            }
            self.save_peers()
            print(f"✅ Pair découvert : {peer_id} ({ip}:{port})")

    def broadcast_discovery(self):
        """Envoie une requête de découverte en broadcast"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Créer le message de découverte
            message = {
                'type': 'DISCOVERY',
                'node_id': self.node_id,
                'ip': self.local_ip,
                'port': self.port
            }

            data = json.dumps(message).encode('utf-8')

            # Envoyer en broadcast sur le réseau local
            broadcast_address = (f"{self.get_network_range()}.255", DISCOVERY_PORT)
            sock.sendto(data, broadcast_address)
            sock.close()
        except Exception as e:
            print(f"❌ Erreur broadcast : {e}")

    def start_discovery_server(self):
        """Démarre le serveur de découverte UDP"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', DISCOVERY_PORT))

        print(f"👂 Serveur découverte lancé sur port {DISCOVERY_PORT}\n")

        while True:
            try:
                data, addr = sock.recvfrom(1024)
                message = json.loads(data.decode('utf-8'))

                if message['type'] == 'DISCOVERY':
                    peer_id = message['node_id']
                    peer_ip = message['ip']
                    peer_port = message['port']

                    # Ajouter le pair découvert
                    self.add_peer(peer_id, peer_ip, peer_port)

            except Exception as e:
                print(f"❌ Erreur serveur découverte : {e}")

    def periodic_discovery(self):
        """Envoie régulièrement des requêtes de découverte"""
        while True:
            time.sleep(DISCOVERY_INTERVAL)
            self.broadcast_discovery()

    def get_peer_files(self, peer_id):
        """Demande la liste des fichiers d'un pair"""
        if peer_id not in self.peers:
            print(f"❌ Pair inconnu : {peer_id}")
            return None

        peer = self.peers[peer_id]
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((peer['ip'], peer['port']))

            sock.sendall(b'LIST')
            data = sock.recv(65536).decode('utf-8')
            files = json.loads(data)

            self.peers[peer_id]['last_seen'] = time.time()
            sock.close()

            return files
        except Exception as e:
            print(f"❌ Erreur requête LIST : {e}")
            return None

    def download_from_peer(self, peer_id, filename, output_path=None):
        """Télécharge un fichier depuis un pair"""
        if peer_id not in self.peers:
            print(f"❌ Pair inconnu : {peer_id}")
            return False

        if output_path is None:
            output_path = os.path.join(SHARED_FOLDER, filename)

        peer = self.peers[peer_id]
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)
            sock.connect((peer['ip'], peer['port']))

            request = f"GET:{filename}".encode('utf-8')
            sock.sendall(request)

            size_data = sock.recv(8)
            if not size_data:
                print("❌ Réponse vide du pair")
                sock.close()
                return False

            file_size = struct.unpack('!Q', size_data)[0]

            if file_size == 0:
                print("❌ Fichier non trouvé sur le pair")
                sock.close()
                return False

            print(f"📥 Téléchargement de {filename} ({file_size / (1024 * 1024):.2f} MB)")

            bytes_received = 0
            with open(output_path, 'wb') as f:
                while bytes_received < file_size:
                    remaining = file_size - bytes_received
                    chunk_size = min(BUFFER_SIZE, remaining)
                    data = sock.recv(chunk_size)

                    if not data:
                        raise Exception("Connexion fermée")

                    f.write(data)
                    bytes_received += len(data)

                    progress = (bytes_received / file_size) * 100
                    bar_length = 30
                    filled = int(bar_length * bytes_received / file_size)
                    bar = '█' * filled + '░' * (bar_length - filled)
                    print(f"[{bar}] {progress:.1f}%", end='\r')

            print(f"\n✅ Fichier téléchargé : {output_path}")
            self.peers[peer_id]['last_seen'] = time.time()
            sock.close()
            self.index_files()
            return True

        except Exception as e:
            print(f"❌ Erreur téléchargement : {e}")
            return False

    def start_tcp_server(self):
        """Démarre le serveur TCP du nœud"""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('0.0.0.0', self.port))
        server_socket.listen(5)

        print(f"🚀 Serveur TCP lancé sur port {self.port}\n")

        try:
            while True:
                client_sock, addr = server_socket.accept()
                thread = threading.Thread(
                    target=self.handle_request,
                    args=(client_sock, addr)
                )
                thread.daemon = True
                thread.start()
        except KeyboardInterrupt:
            print("\n🛑 Serveur arrêté")
        finally:
            server_socket.close()

    def handle_request(self, client_sock, addr):
        """Traite une requête d'un autre nœud"""
        try:
            request = client_sock.recv(1024).decode('utf-8')

            if request == 'LIST':
                self.handle_list(client_sock)
            elif request.startswith('GET:'):
                filename = request[4:]
                self.handle_get(client_sock, filename)

            client_sock.close()
        except Exception as e:
            print(f"❌ Erreur : {e}")
            client_sock.close()

    def handle_list(self, client_sock):
        """Répond avec la liste des fichiers partagés"""
        response = json.dumps(self.shared_files).encode('utf-8')
        client_sock.sendall(response)

    def handle_get(self, client_sock, filename):
        """Envoie un fichier au pair"""
        filepath = os.path.join(SHARED_FOLDER, filename)

        if not os.path.exists(filepath):
            client_sock.sendall(struct.pack('!Q', 0))
            return

        file_size = os.path.getsize(filepath)
        client_sock.sendall(struct.pack('!Q', file_size))

        bytes_sent = 0
        with open(filepath, 'rb') as f:
            while bytes_sent < file_size:
                chunk = f.read(BUFFER_SIZE)
                if not chunk:
                    break
                client_sock.sendall(chunk)
                bytes_sent += len(chunk)

    def show_network_status(self):
        """Affiche l'état du réseau"""
        print("\n" + "=" * 50)
        print("📊 ÉTAT DU RÉSEAU")
        print("=" * 50)
        print(f"Mon ID : {self.node_id}")
        print(f"Mes fichiers : {len(self.shared_files)}")
        for filename in self.shared_files:
            print(f"  - {filename}")

        print(f"\nPairs découverts : {len(self.peers)}")
        for peer_id, info in self.peers.items():
            print(f"  - {peer_id} ({info['ip']}:{info['port']})")
        print("=" * 50 + "\n")


def main():
    """Fonction principale"""

    node = P2PNode(f"node_{socket.gethostname()}_{os.getpid()}", NODE_PORT)

    # Démarrer les services en threads
    discovery_server = threading.Thread(target=node.start_discovery_server)
    discovery_server.daemon = True
    discovery_server.start()

    periodic_discovery = threading.Thread(target=node.periodic_discovery)
    periodic_discovery.daemon = True
    periodic_discovery.start()

    tcp_server = threading.Thread(target=node.start_tcp_server)
    tcp_server.daemon = True
    tcp_server.start()

    # Menu interactif
    while True:
        print("\n=== 📡 MENU P2P ===")
        print("1. État du réseau")
        print("2. Voir mes fichiers")
        print("3. Voir les pairs découverts")
        print("4. Lister les fichiers d'un pair")
        print("5. Télécharger un fichier")
        print("6. Forcer découverte")
        print("7. Quitter")

        choice = input("\nChoisir (1-7) : ").strip()

        if choice == '1':
            node.show_network_status()

        elif choice == '2':
            print("\n📁 Mes fichiers partagés :")
            if node.shared_files:
                for filename, hash_val in node.shared_files.items():
                    filepath = os.path.join(SHARED_FOLDER, filename)
                    size = os.path.getsize(filepath) / (1024 * 1024)
                    print(f"  - {filename} ({size:.2f} MB)")
            else:
                print("  Aucun fichier (mettez-en dans ./shared_files/)")

        elif choice == '3':
            print("\n👥 Pairs découverts :")
            if node.peers:
                for peer_id, info in node.peers.items():
                    print(f"  - {peer_id}")
                    print(f"    IP : {info['ip']}:{info['port']}")
            else:
                print("  Aucun pair trouvé sur le réseau")

        elif choice == '4':
            peer_id = input("ID du pair : ").strip()
            files = node.get_peer_files(peer_id)
            if files:
                print(f"\n📂 Fichiers de {peer_id} :")
                for filename, hash_val in files.items():
                    print(f"  - {filename}")

        elif choice == '5':
            peer_id = input("ID du pair : ").strip()
            filename = input("Nom du fichier : ").strip()
            node.download_from_peer(peer_id, filename)

        elif choice == '6':
            print("🔍 Recherche de pairs...")
            node.broadcast_discovery()
            time.sleep(2)
            print(f"✅ {len(node.peers)} pair(s) trouvé(s)")

        elif choice == '7':
            print("👋 Au revoir !")
            break

        else:
            print("❌ Option invalide")


if __name__ == '__main__':
    main()