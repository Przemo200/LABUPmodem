Lab UP - MODEMY- dr inż. Marek Piasecki - PON 13:15

Aplikacja utrzymuje stały wątek odbiorczy portu COM, który pobiera dane z modemu i przekazuje je do kolejki zdarzeń. UI pobiera zdarzenia co 70 ms i aktualizuje terminal oraz log. Podczas transmisji XMODEM zatrzymuję wątek odbiorczy, aby protokół mógł wyłącznie korzystać z portu, a po zakończeniu przywraca odbiór.
