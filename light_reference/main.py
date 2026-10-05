"""
main.py
-------
Sistemi başlatan ana dosya.
"""
import tkinter as tk
from arayuz import Arayuz


def main():
    kok = tk.Tk()
    arayuz = Arayuz(kok)
    kok.mainloop()


if __name__ == "__main__":
    main()
