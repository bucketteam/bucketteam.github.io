import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib
import json
import subprocess
import requests
import os
import logging
import gettext
import sys

logging.basicConfig(filename='/var/log/grphinstall.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
_ = gettext.gettext

def has_internet():
    try:
        requests.get("https://archlinux.org", timeout=5)
        return True
    except:
        return False

def check_and_install_dependencies():
    required_packages = [
        "python",
        "python-gobject",
        "gtk4",
        "libadwaita",
        "archinstall",
        "python-requests",
        "gparted",
        "weston",
        "seatd"
    ]

    missing = []

    for pkg in required_packages:
        try:
            subprocess.check_call(
                ["pacman", "-Q", pkg],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            missing.append(pkg)

    if not missing:
        logging.info("All required dependencies are present.")
        return True

    logging.warning(f"Missing packages: {', '.join(missing)}")

    if not has_internet():
        logging.error("No internet connection. Cannot install missing packages.")
        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=_("No internet connection"),
            secondary_text=_("Missing dependencies and cannot download them.\nPlease connect to the internet and try again.")
        )
        dialog.run()
        dialog.destroy()
        return False

    try:
        logging.info("Updating package database...")
        subprocess.check_call(["pacman", "-Syy", "--noconfirm"])

        logging.info(f"Installing missing packages: {', '.join(missing)}")
        subprocess.check_call(["pacman", "-S", "--noconfirm"] + missing)

        logging.info("Dependencies installed successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to install dependencies: {str(e)}")
        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=_("Failed to install dependencies"),
            secondary_text=_("Installation failed. Check /var/log/grphinstall.log for details.")
        )
        dialog.run()
        dialog.destroy()
        return False

class BasePage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.app = app
        self.build_ui()
        self.app.assistant.append_page(self)
        self.app.assistant.set_page_title(self, self.title)
        self.app.assistant.set_page_complete(self, True)

    def build_ui(self):
        pass

    def update_config(self):
        pass

class WelcomePage(BasePage):
    title = "Welcome"
    def build_ui(self):
        label = Gtk.Label(label=_("Welcome to grphinstall - Arch Linux Installer"))
        self.append(label)

class LocalePage(BasePage):
    title = "Locales & Timezone"
    def build_ui(self):
        locale_combo = Gtk.ComboBoxText()
        locale_combo.connect('changed', lambda c: self.app.config.update({'locale': c.get_active_text()}))
        self.append(locale_combo)

class MirrorPage(BasePage):
    title = "Mirrors"
    def build_ui(self):
        self.mirrors = self.fetch_mirrors()
        multilib_check = Gtk.CheckButton(label=_("Enable Multilib Repo"))
        multilib_check.connect('toggled', lambda w: self.app.config.update({'additional-repositories': ['multilib'] if w.get_active() else []}))
        self.append(multilib_check)

    def fetch_mirrors(self):
        try:
            response = requests.get('https://archlinux.org/mirrors/status/json/')
            return [{'country': m['country'], 'url': m['url']} for m in response.json()['urls'] if m['completion_pct'] == 1.0]
        except:
            logging.warning("Offline: Using default mirrors")
            return []

class DiskPage(BasePage):
    title = "Disk & Partitioning"
    def build_ui(self):
        self.preview_label = Gtk.Label(label=_("Preview: No changes yet"))
        self.append(self.preview_label)
        
        swap_toggle = Gtk.CheckButton(label=_("Enable Swap"))
        swap_toggle.connect('toggled', self.update_swap)
        self.append(swap_toggle)
        
        gparted_button = Gtk.Button(label=_("Advanced Partition Editor (GParted)"))
        gparted_button.connect('clicked', self.launch_gparted)
        self.append(gparted_button)
        
        manual_button = Gtk.Button(label=_("Apply Manual Changes"))
        manual_button.connect('clicked', self.parse_partitions)
        self.append(manual_button)

    def update_swap(self, widget):
        self.app.config['swap'] = widget.get_active()
        self.update_preview()

    def launch_gparted(self, button):
        try:
            subprocess.run(['gparted'])
            self.refresh_lsblk()
            self.update_preview()
        except:
            logging.error("GParted launch failed")
            self.app.show_error(_("GParted not available"))

    def parse_partitions(self, button):
        output = subprocess.check_output(['lsblk', '-o', 'NAME,MOUNTPOINT,FSTYPE,SIZE', '-J']).decode()
        disks = json.loads(output)['blockdevices']
        layouts = {}
        for disk in disks:
            parts = []
            for child in disk.get('children', []):
                parts.append({
                    'mountpoint': child.get('mountpoint', ''),
                    'filesystem': child.get('fstype', ''),
                    'size': child.get('size', '')
                })
            layouts[f"/dev/{disk['name']}"] = {'partitions': parts}
        self.app.config['disk_layouts'] = layouts
        self.update_preview()

    def update_preview(self):
        preview = json.dumps(self.app.config.get('disk_layouts', {}), indent=2)
        self.preview_label.set_text(_("Preview:\n") + preview)

    def refresh_lsblk(self):
        pass

class SummaryPage(BasePage):
    title = "Summary"
    def build_ui(self):
        self.summary_text = Gtk.TextView()
        self.append(self.summary_text)
        save_button = Gtk.Button(label=_("Save Config"))
        save_button.connect('clicked', self.app.save_config)
        self.append(save_button)
        dry_run_check = Gtk.CheckButton(label=_("Dry Run (Test Only)"))
        self.append(dry_run_check)
        self.app.assistant.connect('prepare', lambda a, p: self.update_summary() if p is self else None)

    def update_summary(self):
        buffer = self.summary_text.get_buffer()
        buffer.set_text(json.dumps(self.app.config, indent=2))

class InstallerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.grphinstall.installer')
        self.config = {'locale': 'en_US.UTF-8', 'hostname': 'archlinux', 'timezone': 'UTC', 'bootloader': 'grub', 'kernel': 'linux', 'swap': False, 'network': 'copy_iso'}
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_title(_("grphinstall"))
        self.window.set_default_size(800, 600)

        self.assistant = Gtk.Assistant()
        self.assistant.connect('cancel', self.on_cancel)
        self.assistant.connect('apply', self.on_apply)
        self.window.set_child(self.assistant)

        WelcomePage(self)
        LocalePage(self)
        MirrorPage(self)
        DiskPage(self)
        SummaryPage(self)

        self.window.present()

    def on_apply(self, assistant):
        if not self.validate_config():
            self.show_error(_("Invalid config - check pages"))
            return
        confirm = Gtk.MessageDialog(transient_for=self.window, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.YES_NO, text=_("Confirm?"))
        if confirm.run() == Gtk.ResponseType.YES:
            with open('/tmp/config.json', 'w') as f:
                json.dump(self.config, f)
            progress_dialog = self.show_progress()
            args = ['archinstall', '--config', '/tmp/config.json', '--debug']
            if self.dry_run:
                args.append('--dry-run')
            try:
                process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                GLib.io_add_watch(process.stdout, GLib.IO_IN, self.log_output)
                while process.poll() is None:
                    GLib.usleep(100000)
                if process.returncode != 0:
                    raise Exception
            except Exception as e:
                logging.error(str(e))
                self.show_error(_("Install failed"))
            finally:
                progress_dialog.destroy()
                self.show_info(_("Install complete!"))

    def log_output(self, fd, condition):
        line = os.read(fd.fileno(), 1024).decode()
        logging.info(line.strip())
        return True

    def show_progress(self):
        dialog = Gtk.Dialog(transient_for=self.window, modal=True, title=_("Installing..."))
        progress = Gtk.ProgressBar(pulse_step=0.05)
        dialog.get_content_area().append(progress)
        dialog.show()
        def pulse(): progress.pulse(); return True
        self.pulse_id = GLib.timeout_add(100, pulse)
        return dialog

    def validate_config(self):
        required = ['locale', 'hostname', 'disk_layouts']
        return all(key in self.config for key in required)

    def show_error(self, msg):
        dialog = Gtk.MessageDialog(transient_for=self.window, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=msg)
        dialog.run(); dialog.destroy()

    def show_info(self, msg):
        dialog = Gtk.MessageDialog(transient_for=self.window, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=msg)
        dialog.run(); dialog.destroy()

    def save_config(self, button):
        pass

    def on_cancel(self, assistant):
        self.window.destroy()

if __name__ == '__main__':
    if not check_and_install_dependencies():
        sys.exit(1)

    app = InstallerApp()
    app.run(None)
