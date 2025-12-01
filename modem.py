import tkinter as tk                               # główna biblioteka GUI
from tkinter import ttk, filedialog, messagebox    # widgety, dialogi plików i okienka
import serial                                      # obsługa portów COM (pySerial)
import serial.tools.list_ports                     # pobieranie dostępnych portów
from xmodem import XMODEM                          # implementacja protokołu XMODEM
import threading                                   # obsługa wątków (czytnik COM)
import queue                                       # FIFO do komunikacji z wątku
import time                                        # opóźnienia dla wątku i XMODEM
import os                                          # operacje na plikach i ścieżkach



# KLASA GŁÓWNA APLIKACJI
class FullModemInterface(tk.Tk):
    """
    Apka zawiera:
    - konfigurację portu COM
    - terminal komunikacyjny
    - pełny log systemowy
    - obsługę komend AT (ATD, ATA, ATH)
    - wysyłanie zwykłego tekstu
    - przesyłanie plików protokołem XMODEM
    - kolejkę zdarzeń do bezpiecznego UI (bez blokowania GUI)
    - osobny wątek do odbioru danych z modemu
    """

    def __init__(self):
        super().__init__()

        # ustawienia okna
        self.title("Interfejs modemowy – terminal + XMODEM")
        self.geometry("1120x650")   # większe, szerokie okno

        # zmienne wewnętrzne
        self.com_ref = None                 # obiekt pySerial.Serial
        self.com_thread = None              # wątek odbiorczy
        self.stop_flag = threading.Event()  # flaga zatrzymania wątku
        self.tasks = queue.Queue()          # kolejka zdarzeń kierowanych do GUI

        # budowanie interfejsu użytkownika
        self.__build_window()

        # uruchomi cykliczny dispatcher kolejki zdarzeń
        self.__pump_ui_queue()

    # GUI

    def __build_window(self):
        """
        Buduje układ graficzny.
        """

        #  GÓRNY PANEL – USTAWIENIA COM
        top_frame = ttk.Frame(self, padding=6)
        top_frame.pack(fill="x")

        # pobranie listy portów COM
        ports = [p.device for p in serial.tools.list_ports.comports()]

        self.var_port   = tk.StringVar(value=(ports[0] if ports else ""))   # COMx
        self.var_baud   = tk.StringVar(value="9600")                        # prędkość
        self.var_bits   = tk.StringVar(value="8")                           # bity danych
        self.var_stop   = tk.StringVar(value="1")                           # stop bits
        self.var_parity = tk.StringVar(value="N")                           # parzystość

        # pierwszy segment – port i prędkość
        ttk.Label(top_frame, text="Port:").grid(row=0, column=0, sticky="e")
        ttk.Combobox(top_frame, textvariable=self.var_port, values=ports,
                     width=10, state="readonly").grid(row=0, column=1, padx=4)

        ttk.Label(top_frame, text="Baud:").grid(row=0, column=2, sticky="e")
        ttk.Combobox(top_frame, textvariable=self.var_baud,
                     values=["4800","9600","19200","38400","57600","115200"],
                     width=10, state="readonly").grid(row=0, column=3, padx=4)

        # drugi segment – bity danych, stop i parzystość
        ttk.Label(top_frame, text="Bity:").grid(row=0, column=4, sticky="e")
        ttk.Combobox(top_frame, textvariable=self.var_bits, values=["8","7","6","5"],
                     width=5, state="readonly").grid(row=0, column=5, padx=4)

        ttk.Label(top_frame, text="Stop:").grid(row=0, column=6, sticky="e")
        ttk.Combobox(top_frame, textvariable=self.var_stop, values=["1","2"],
                     width=5, state="readonly").grid(row=0, column=7, padx=4)

        ttk.Label(top_frame, text="Parity:").grid(row=0, column=8, sticky="e")
        ttk.Combobox(top_frame, textvariable=self.var_parity,
                     values=["N","E","O","M","S"],
                     width=5, state="readonly").grid(row=0, column=9, padx=4)

        # przycisk połącz / rozłącz
        self.btn_link = ttk.Button(top_frame, text="Połącz", width=12,
                                   command=self.__toggle_connection)
        self.btn_link.grid(row=0, column=10, padx=8)

        # etykieta stanu
        self.lbl_state = ttk.Label(top_frame, text="Rozłączony", foreground="red")
        self.lbl_state.grid(row=0, column=11, padx=6)

        # ŚRODEK – TERMINAL
        middle = ttk.Frame(self)
        middle.pack(fill="both", expand=True)

        # Po lewej notebook z dwoma terminalami
        left_side = ttk.Notebook(middle)
        left_side.pack(side="left", fill="both", expand=True, padx=6, pady=6)

        # Terminal sesji wysyłanie/odbiór
        self.page_session = ttk.Frame(left_side)
        left_side.add(self.page_session, text="Sesja komunikacyjna")

        self.txt_session = tk.Text(self.page_session, wrap="word")
        self.txt_session.pack(fill="both", expand=True)
        self.txt_session.config(state="disabled")

        # Pełny log wszystko łącznie z AT
        self.page_log = ttk.Frame(left_side)
        left_side.add(self.page_log, text="Pełny log")

        self.txt_log = tk.Text(self.page_log, wrap="word", background="#111", foreground="#0F0")
        self.txt_log.pack(fill="both", expand=True)
        self.txt_log.config(state="disabled")

        # Po prawej panel komend AT
        right_cmd = ttk.LabelFrame(middle, text="Komendy modemu AT")
        right_cmd.pack(side="left", fill="y", padx=10, pady=6)

        # pole numeru do wybrania
        ttk.Label(right_cmd, text="Numer dla ATD:").pack(anchor="w", pady=(5,1))
        self.var_number = tk.StringVar()
        ttk.Entry(right_cmd, textvariable=self.var_number).pack(fill="x", padx=4)

        # pole zwykłej wiadomości
        ttk.Label(right_cmd, text="Wyślij tekst:").pack(anchor="w", pady=(10,1))
        self.var_message = tk.StringVar()
        ttk.Entry(right_cmd, textvariable=self.var_message).pack(fill="x", padx=4)

        # przyciski AT
        self.btn_dial = ttk.Button(right_cmd, text="ATD – Zadzwoń",
                                  command=self.__send_dial, state="disabled")
        self.btn_ans = ttk.Button(right_cmd, text="ATA – Odbierz",
                                  command=self.__send_answer, state="disabled")
        self.btn_hng = ttk.Button(right_cmd, text="ATH – Rozłącz",
                                  command=self.__send_hang, state="disabled")
        self.btn_txt = ttk.Button(right_cmd, text="Wyślij tekst",
                                  command=self.__send_text, state="disabled")

        self.btn_dial.pack(fill="x", pady=3, padx=4)
        self.btn_ans.pack(fill="x", pady=3, padx=4)
        self.btn_hng.pack(fill="x", pady=3, padx=4)
        self.btn_txt.pack(fill="x", pady=3, padx=4)

        # DÓŁ – MODUŁ XMODEM (SEND/RECV)
        bottom = ttk.LabelFrame(self, text="Przesyłanie pliku XMODEM")
        bottom.pack(fill="x", padx=8, pady=8)

        self.var_path = tk.StringVar()

        # ścieżka do pliku
        ttk.Entry(bottom, textvariable=self.var_path).grid(row=0, column=0,
                                                           padx=5, pady=5,
                                                           sticky="ew")
        bottom.columnconfigure(0, weight=1)

        ttk.Button(bottom, text="Wybierz…",
                   command=self.__pick_file).grid(row=0, column=1, padx=5)

        # przyciski wysyłania/odbioru
        self.btn_xsend = ttk.Button(bottom, text="Wyślij XMODEM",
                                    state="disabled", command=self.__start_send)
        self.btn_xrecv = ttk.Button(bottom, text="Odbierz XMODEM",
                                    state="disabled", command=self.__start_recv)

        self.btn_xsend.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        self.btn_xrecv.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        # pasek postępu
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

    # OBSŁUGA KOLEJKI ZDARZEŃ DO GUI
    def __pump_ui_queue(self):
        """
        Co ~70 ms sprawdza czy w kolejce są nowe zdarzenia
        wysłane z wątku odbioru COM lub z XMODEM.
        """
        try:
            while True:
                ev = self.tasks.get_nowait()
                code = ev[0]

                # dopisywanie do logu ogólnego
                if code == "log":
                    msg = ev[1]
                    self.txt_log.config(state="normal")
                    self.txt_log.insert("end", msg + "\n")
                    self.txt_log.see("end")
                    self.txt_log.config(state="disabled")

                # dopisywanie do terminala sesji
                elif code == "session":
                    msg = ev[1]
                    self.txt_session.config(state="normal")
                    self.txt_session.insert("end", msg)
                    self.txt_session.see("end")
                    self.txt_session.config(state="disabled")

                # aktualizacja stanu COM
                elif code == "status":
                    txt, color = ev[1], ev[2]
                    self.lbl_state.config(text=txt, foreground=color)

                # sterowanie przyciskami AT/XMODEM
                elif code == "enable":
                    state_val = ev[1]
                    self.btn_dial.config(state=state_val)
                    self.btn_ans.config(state=state_val)
                    self.btn_hng.config(state=state_val)
                    self.btn_txt.config(state=state_val)
                    self.btn_xsend.config(state=state_val)
                    self.btn_xrecv.config(state=state_val)

                # aktualizacja progressbaru
                elif code == "pg":
                    self.progress['value'] = ev[1]

        except queue.Empty:
            pass

        self.after(70, self.__pump_ui_queue)

    # OBSŁUGA PORTU SZEREGOWEGO

    def __toggle_connection(self):
        """Łączy lub rozłącza port COM."""
        if self.com_ref and self.com_ref.is_open:
            self.__disconnect()
        else:
            self.__connect()


    def __connect(self):
        """Próbuje otworzyć port COM."""
        try:
            self.com_ref = serial.Serial(
                port=self.var_port.get(),
                baudrate=int(self.var_baud.get()),
                bytesize=int(self.var_bits.get()),
                parity=self.var_parity.get(),
                stopbits=int(self.var_stop.get()),
                timeout=0.1
            )
        except Exception as e:
            messagebox.showerror("Błąd", f"Nie można otworzyć portu:\n{e}")
            return

        self.tasks.put(("status", "Połączony", "green"))
        self.tasks.put(("log", f"Otwarto port {self.var_port.get()}"))
        self.btn_link.config(text="Rozłącz")

        # aktywacja przycisków
        self.tasks.put(("enable", "normal"))

        # uruchom wątek odbioru
        self.stop_flag.clear()
        self.com_thread = threading.Thread(target=self.__reader_loop, daemon=True)
        self.com_thread.start()


    def __disconnect(self):
        """Zamyka połączenie COM i zatrzymuje wątek."""
        self.stop_flag.set()
        time.sleep(0.2)

        try:
            self.com_ref.close()
        except:
            pass

        self.tasks.put(("status", "Rozłączony", "red"))
        self.tasks.put(("log", "Port został zamknięty"))
        self.btn_link.config(text="Połącz")
        self.tasks.put(("enable", "disabled"))


    def __reader_loop(self):
        """Wątek odbierający dane z modemu."""
        while not self.stop_flag.is_set():
            try:
                block = self.com_ref.read(self.com_ref.in_waiting or 1)
                if block:
                    txt = block.decode("ascii", errors="replace")
                    self.tasks.put(("session", txt))
            except:
                break
            time.sleep(0.03)

    # OBSŁUGA KOMEND AT
    def __write_at(self, cmd):
        """Wysyła komendę AT do modemu."""
        if not self.com_ref or not self.com_ref.is_open:
            self.tasks.put(("log", "Błąd: port COM zamknięty"))
            return

        full = cmd + "\r\n"
        self.com_ref.write(full.encode("ascii"))
        self.tasks.put(("log", f">>> {cmd}"))

    def __send_dial(self):
        self.__write_at("ATD" + self.var_number.get())

    def __send_answer(self):
        self.__write_at("ATA")

    def __send_hang(self):
        self.__write_at("+++ATH")

    def __send_text(self):
        self.__write_at(self.var_message.get())

    # OBSŁUGA PLIKÓW I XMODEM
    def __pick_file(self):
        """Okno wyboru pliku."""
        p = filedialog.askopenfilename()
        if p:
            self.var_path.set(p)
            self.tasks.put(("log", f"Wybrano: {p}"))

    # funkcje XMODEM getc i putc
    def __getc(self, size, timeout=1):
        """Funkcja odbioru danych dla XMODEM."""
        if not self.com_ref:
            return None
        self.com_ref.timeout = timeout
        return self.com_ref.read(size) or None

    def __putc(self, data, timeout=1):
        """Funkcja wysyłania danych dla XMODEM."""
        if not self.com_ref:
            return 0
        self.com_ref.write_timeout = timeout
        return self.com_ref.write(data)

    def __stop_reader_for_xfer(self):
        """Zatrzymuje wątek COM na czas transmisji XMODEM."""
        self.stop_flag.set()
        time.sleep(0.3)

    def __restore_reader(self):
        """Wznawia wątek odczytu COM po XMODEM."""
        self.stop_flag.clear()
        self.com_thread = threading.Thread(target=self.__reader_loop, daemon=True)
        self.com_thread.start()

    # start wysyłania
    def __start_send(self):
        """Uruchamia wątek wysyłania XMODEM."""
        path = self.var_path.get()
        if not os.path.exists(path):
            messagebox.showwarning("Błąd", "Plik nie istnieje.")
            return

        self.__stop_reader_for_xfer()
        self.tasks.put(("enable", "disabled"))
        self.tasks.put(("log", "Rozpoczynam wysyłanie XMODEM..."))

        threading.Thread(target=self.__send_file_thread, args=(path,), daemon=True).start()

    def __send_file_thread(self, path):
        modem = XMODEM(self.__getc, self.__putc)

        # pasek progressu
        def prog(total, ok, err):
            if total > 0:
                self.tasks.put(("pg", (ok/total)*100))

        try:
            # plik
            with open(path, "rb") as f:
                ok = modem.send(f, callback=prog, retry=1000)
            if ok:
                self.tasks.put(("log", "Plik wysłano poprawnie."))
            else:
                self.tasks.put(("log", "Błąd wysyłania XMODEM."))

        except Exception as e:
            self.tasks.put(("log", f"Błąd XMODEM: {e}"))

        self.tasks.put(("enable", "normal"))
        self.tasks.put(("pg", 0))
        self.__restore_reader()

    # start odbioru
    def __start_recv(self):
        """Uruchamia wątek odbioru pliku XMODEM."""
        save_path = filedialog.asksaveasfilename()
        if not save_path:
            return

        self.__stop_reader_for_xfer()
        self.tasks.put(("enable", "disabled"))
        self.tasks.put(("log", f"Gotowy do odbioru XMODEM (zapis: {save_path})..."))

        threading.Thread(target=self.__recv_file_thread, args=(save_path,), daemon=True).start()

    def __recv_file_thread(self, path):
        modem = XMODEM(self.__getc, self.__putc)

        try:
            # plik
            with open(path, "wb") as f:
                ok = modem.recv(f, crc_mode=False)

            if ok:
                self.tasks.put(("log", f"Odebrano poprawnie. Zapisano do: {path}"))
            else:
                self.tasks.put(("log", "Odbiór XMODEM nieudany."))
                if os.path.exists(path):
                    os.remove(path)

        except Exception as e:
            self.tasks.put(("log", f"Błąd odbioru XMODEM: {e}"))

        self.tasks.put(("enable", "normal"))
        self.tasks.put(("pg", 0))
        self.__restore_reader()

# URUCHOMIENIE

if __name__ == "__main__":
    app = FullModemInterface()
    app.mainloop()