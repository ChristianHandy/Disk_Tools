from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QPushButton,
    QMessageBox
)
import subprocess
import os

class RemoteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remote verbinden")
        self.setMinimumWidth(400)

        layout = QVBoxLayout()
        form = QFormLayout()

        self.protocol = QComboBox()
        self.protocol.addItems(["sshfs", "smb"])

        self.host = QLineEdit()
        self.user = QLineEdit()
        self.remote_path = QLineEdit()
        self.local_path = QLineEdit()

        form.addRow("Protokoll", self.protocol)
        form.addRow("Host", self.host)
        form.addRow("Benutzer", self.user)
        form.addRow("Remote-Pfad", self.remote_path)
        form.addRow("Lokaler Mountpunkt", self.local_path)

        self.connect_btn = QPushButton("Verbinden")
        self.connect_btn.clicked.connect(self.try_mount)

        layout.addLayout(form)
        layout.addWidget(self.connect_btn)
        self.setLayout(layout)

    def try_mount(self):
        proto = self.protocol.currentText()
        host = self.host.text()
        user = self.user.text()
        remote = self.remote_path.text()
        local = self.local_path.text()

        success, msg = mount_remote(proto, host, user, remote, local)
        if success:
            QMessageBox.information(self, "Erfolg", "Verbindung hergestellt.")
            self.accept()
        else:
            QMessageBox.critical(self, "Fehler", f"Verbindung fehlgeschlagen:\n{msg}")

def mount_remote(protocol, host, user, remote_path, local_path):
    try:
        os.makedirs(local_path, exist_ok=True)

        if protocol == "sshfs":
            cmd = [
                "sshfs",
                f"{user}@{host}:{remote_path}",
                local_path,
                "-o", "allow_other"
            ]
        elif protocol == "smb":
            cmd = [
                "sudo", "mount", "-t", "cifs",
                f"//{host}/{remote_path}",
                local_path,
                "-o", f"username={user},rw"
            ]
        else:
            return False, "Unbekanntes Protokoll."

        subprocess.run(cmd, check=True)
        return True, "Mount erfolgreich."
    except subprocess.CalledProcessError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)

def show_dialog(main_window):
    dialog = RemoteDialog(main_window)
    dialog.exec()
