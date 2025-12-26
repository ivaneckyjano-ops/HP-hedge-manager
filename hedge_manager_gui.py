#!/usr/bin/env python3
"""
Hedge Manager GUI - Kompletný nástroj pre správu opčných spread pozícií

Funkcie:
1. Kontrola pripojenia k TWS
2. Nájsť nový hedge (short + long PUT/CALL)
3. Vypočítať exit ceny a stop-loss úrovne
4. Monitorovať existujúcu pozíciu
5. Margin Optimizer - optimalizácia margin/ROI
6. Scenárová analýza - What-if simulácie
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import subprocess
import threading
import json
import os
import sys
from datetime import datetime, date
import math

try:
    from scipy.stats import norm
    from scipy.optimize import brentq
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

# Import lokálnych modulov pre scenáre
try:
    sys.path.insert(0, '/home/narbon/Aplikácie/tws-webapp/scripts')
    from scenario_simulator import ScenarioSimulator
    SCENARIO_AVAILABLE = True
except ImportError:
    SCENARIO_AVAILABLE = False

try:
    from export_utils import export_strategy, ExportUtils
    EXPORT_AVAILABLE = True
except ImportError:
    EXPORT_AVAILABLE = False

# Optional plotting support
try:
    import matplotlib
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False


class HedgeManagerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Hedge Manager - PUT Spread Calculator")
        self.root.geometry("900x750")
        
        # Archív nastavení
        self.settings_file = '/home/narbon/Aplikácie/tws-webapp/settings_archive.json'
        self.saved_strategies = {}
        
        # Premenné
        self.symbol_var = tk.StringVar(value="SPY")
        self.port_var = tk.StringVar(value="7496")
        self.min_premium_var = tk.StringVar(value="0.70")
        self.short_expiry_var = tk.StringVar()
        self.long_expiry_var = tk.StringVar()
        
        # ATR nastavenia
        self.atr_7d = None
        self.atr_last_updated = None
        self.atr_multiplier_var = tk.DoubleVar(value=1.0)  # násobok ATR pre varovanie (1.0 - 3.0 step 0.2)
        
        # Stoploss na cenu opcie (násobok pôvodnej ceny)
        self.option_stoploss_mult_var = tk.DoubleVar(value=2.0)  # default 2x = ak opcia stojí 2x viac, roll
        
        # Pre position monitor
        self.short_strike_var = tk.StringVar()
        self.roll_trigger_var = tk.StringVar(value="-0.30")
        
        # Typ opcie (PUT/CALL)
        self.option_type_var = tk.StringVar(value="PUT")
        
        # Pre výpočty
        self.iv_var = tk.StringVar(value="0.18")
        self.rate_var = tk.StringVar(value="0.05")
        
        # Pre Margin Optimizer
        self.broker_var = tk.StringVar(value="IBKR")
        self.max_margin_var = tk.StringVar(value="5000")
        self.min_roi_var = tk.StringVar(value="3.0")
        self.dte_offsets_var = tk.StringVar(value="0,7,14,21,30")
        
        # Pre Spread Kalkulátor (manuálne zadanie)
        self.calc_short_strike_var = tk.StringVar()
        self.calc_short_expiry_var = tk.StringVar()
        self.calc_short_premium_var = tk.StringVar()
        self.calc_long_strike_var = tk.StringVar()
        self.calc_long_expiry_var = tk.StringVar()
        self.calc_long_premium_var = tk.StringVar()
        self.calc_underlying_price_var = tk.StringVar()
        
        # Balancer (samostatné polia)
        self.bal_long_type_var = tk.StringVar(value="CALL")
        self.bal_long_strike_var = tk.StringVar()
        self.bal_long_expiry_var = tk.StringVar()
        self.bal_long_premium_var = tk.StringVar()
        self.bal_underlying_var = tk.StringVar()
        self.bal_iv_var = tk.StringVar(value=self.iv_var.get())
        self.bal_opposite_type_var = tk.StringVar(value="PUT")
        self.bal_opposite_strike_var = tk.StringVar()
        self.bal_opposite_premium_var = tk.StringVar()
        self.bal_last_analysis = None
        self.bal_type_note_var = tk.StringVar(value="")
        # Keep bal_last_analysis in sync if user edits the suggested strike
        self.bal_opposite_strike_var.trace_add('write', lambda *args: self.on_bal_opposite_strike_changed())
        # Plotting controls
        self.bal_plot_metric_var = tk.StringVar(value='Price')
        self.bal_plot_atr_mult_var = tk.DoubleVar(value=1.0)
        
        # Trace pre automatické prepočítavanie stoploss
        self.calc_short_strike_var.trace_add('write', lambda *args: self.auto_recalc_stoploss())
        self.calc_short_expiry_var.trace_add('write', lambda *args: self.auto_recalc_stoploss())
        self.iv_var.trace_add('write', lambda *args: self.auto_recalc_stoploss())
        
        # Connection status
        self.connected = False
        self.connection_info = {}
        
        # Výsledky
        self.last_result = None
        self.alternatives = []
        self.scenarios = None
        
        # Stop flag pre optimalizáciu
        self.stop_optimization_flag = False
        self.optimization_process = None
        
        # Pre interaktívny optimizer
        self.available_expiries = []
        
        # Pre Roll Optimizer
        self.roll_current_strike_var = tk.StringVar()
        self.roll_current_expiry_var = tk.StringVar()
        self.roll_current_premium_var = tk.StringVar()
        self.roll_current_dte_var = tk.StringVar()
        self.roll_underlying_var = tk.StringVar()
        self.roll_total_invested_var = tk.StringVar(value="0")  # Celková investícia do pozície
        self.roll_received_credit_var = tk.StringVar(value="0")  # Už prijatý kredit (z expirovaného shortu)
        
        self.create_widgets()
        self.check_connection()  # Kontrola pripojenia pri štarte
    
    def create_widgets(self):
        # === CONNECTION STATUS BAR ===
        self.create_status_bar()
        
        # Notebook pre záložky
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True, padx=5, pady=5)
        
        # === TAB 1: Connection ===
        tab1 = ttk.Frame(notebook)
        notebook.add(tab1, text="🔌 Pripojenie")
        self.create_connection_tab(tab1)
        
        # === TAB 2: Spread Kalkulátor ===
        tab2 = ttk.Frame(notebook)
        notebook.add(tab2, text="🧮 Kalkulátor")
        self.create_spread_calculator_tab(tab2)
        
        # === TAB 3: Interaktívny Optimizer ===
        tab3 = ttk.Frame(notebook)
        notebook.add(tab3, text="🔧 Optimizer")
        self.create_interactive_optimizer_tab(tab3)
        
        # === TAB 3.5: Balancer ===
        tab3b = ttk.Frame(notebook)
        notebook.add(tab3b, text="⚖️ Balancer")
        self.create_balancer_tab(tab3b)
        
        # === TAB 4: Scenáre ===
        tab4 = ttk.Frame(notebook)
        notebook.add(tab4, text="📈 Scenáre")
        self.create_scenarios_tab(tab4)
        
        # === TAB 5: Position Monitor ===
        tab5 = ttk.Frame(notebook)
        notebook.add(tab5, text="👁️ Monitor")
        self.create_monitor_tab(tab5)
        
        # === TAB 6: Roll Optimizer ===
        tab6 = ttk.Frame(notebook)
        notebook.add(tab6, text="🔄 Roll Optimizer")
        self.create_roll_optimizer_tab(tab6)
        
        # === TAB 7: Archív Stratégií ===
        tab7 = ttk.Frame(notebook)
        notebook.add(tab7, text="📁 Archív")
        self.create_archive_tab(tab7)
    
    def create_find_hedge_tab(self, parent):
        """Záložka pre hľadanie nového hedge"""
        frame = ttk.LabelFrame(parent, text="Parametre hľadania", padding=10)
        frame.pack(fill='x', padx=10, pady=10)
        
        # Riadok 1
        row1 = ttk.Frame(frame)
        row1.pack(fill='x', pady=5)
        
        ttk.Label(row1, text="Symbol:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.symbol_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Min Premium $:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.min_premium_var, width=8).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Typ:").pack(side='left', padx=5)
        ttk.Combobox(row1, textvariable=self.option_type_var, values=["PUT", "CALL"], width=6).pack(side='left', padx=5)
        
        # Riadok 2
        row2 = ttk.Frame(frame)
        row2.pack(fill='x', pady=5)
        
        ttk.Label(row2, text="Short Expiry:").pack(side='left', padx=5)
        self.short_expiry_combo = ttk.Combobox(row2, textvariable=self.short_expiry_var, width=12)
        self.short_expiry_combo.pack(side='left', padx=5)
        
        ttk.Label(row2, text="Long Expiry:").pack(side='left', padx=5)
        self.long_expiry_combo = ttk.Combobox(row2, textvariable=self.long_expiry_var, width=12)
        self.long_expiry_combo.pack(side='left', padx=5)
        
        ttk.Button(row2, text="🔄 Načítať expirácie", command=self.load_expiries).pack(side='left', padx=10)
        
        # Tlačidlo hľadať
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill='x', pady=10)
        
        self.find_btn = ttk.Button(btn_frame, text="🔍 NÁJSŤ HEDGE", command=self.find_hedge)
        self.find_btn.pack(side='left', padx=5)
        
        self.status_label = ttk.Label(btn_frame, text="Pripravené")
        self.status_label.pack(side='left', padx=20)
        
        # Výsledky
        result_frame = ttk.LabelFrame(parent, text="Výsledok", padding=10)
        result_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.hedge_result_text = scrolledtext.ScrolledText(result_frame, height=20, font=('Courier', 10))
        self.hedge_result_text.pack(fill='both', expand=True)
    
    def create_exit_calc_tab(self, parent):
        """Záložka pre výpočet exit cien"""
        frame = ttk.LabelFrame(parent, text="Parametre pozície", padding=10)
        frame.pack(fill='x', padx=10, pady=10)
        
        # Riadok 1
        row1 = ttk.Frame(frame)
        row1.pack(fill='x', pady=5)
        
        ttk.Label(row1, text="Symbol:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.symbol_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Short Strike:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.short_strike_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Expirácia:").pack(side='left', padx=5)
        self.exit_expiry_combo = ttk.Combobox(row1, textvariable=self.short_expiry_var, width=12)
        self.exit_expiry_combo.pack(side='left', padx=5)
        
        ttk.Label(row1, text="Typ:").pack(side='left', padx=5)
        ttk.Combobox(row1, textvariable=self.option_type_var, values=["PUT", "CALL"], width=6).pack(side='left', padx=5)
        
        # Riadok 2
        row2 = ttk.Frame(frame)
        row2.pack(fill='x', pady=5)
        
        ttk.Label(row2, text="IV (napr. 0.18):").pack(side='left', padx=5)
        ttk.Entry(row2, textvariable=self.iv_var, width=8).pack(side='left', padx=5)
        
        ttk.Label(row2, text="Roll Trigger Delta:").pack(side='left', padx=5)
        ttk.Entry(row2, textvariable=self.roll_trigger_var, width=8).pack(side='left', padx=5)
        
        # Tlačidlá
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill='x', pady=10)
        
        ttk.Button(btn_frame, text="📊 VYPOČÍTAŤ EXIT CENY", command=self.calculate_exit_prices).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="🔄 Načítať z TWS", command=self.load_from_tws).pack(side='left', padx=5)
        
        # Tabuľka výsledkov
        result_frame = ttk.LabelFrame(parent, text="Exit Ceny (Stop-Loss úrovne)", padding=10)
        result_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Treeview pre tabuľku
        columns = ('delta', 'underlying', 'option_price', 'action')
        self.exit_tree = ttk.Treeview(result_frame, columns=columns, show='headings', height=8)
        
        self.exit_tree.heading('delta', text='Delta')
        self.exit_tree.heading('underlying', text='Cena podkladu')
        self.exit_tree.heading('option_price', text='Cena opcie')
        self.exit_tree.heading('action', text='Akcia')
        
        self.exit_tree.column('delta', width=80, anchor='center')
        self.exit_tree.column('underlying', width=120, anchor='center')
        self.exit_tree.column('option_price', width=120, anchor='center')
        self.exit_tree.column('action', width=150, anchor='center')
        
        self.exit_tree.pack(fill='both', expand=True)
        
        # Odporúčania
        rec_frame = ttk.LabelFrame(parent, text="📋 Pre nastavenie v brokeri", padding=10)
        rec_frame.pack(fill='x', padx=10, pady=10)
        
        self.recommendations_text = tk.Text(rec_frame, height=6, font=('Courier', 11))
        self.recommendations_text.pack(fill='x')
    
    def create_monitor_tab(self, parent):
        """Záložka pre monitoring pozície"""
        frame = ttk.LabelFrame(parent, text="Parametre monitoringu", padding=10)
        frame.pack(fill='x', padx=10, pady=10)
        
        row1 = ttk.Frame(frame)
        row1.pack(fill='x', pady=5)
        
        ttk.Label(row1, text="Symbol:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.symbol_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Short Strike:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.short_strike_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Expirácia:").pack(side='left', padx=5)
        self.monitor_expiry_combo = ttk.Combobox(row1, textvariable=self.short_expiry_var, width=12)
        self.monitor_expiry_combo.pack(side='left', padx=5)
        
        ttk.Label(row1, text="Typ:").pack(side='left', padx=5)
        ttk.Combobox(row1, textvariable=self.option_type_var, values=["PUT", "CALL"], width=6).pack(side='left', padx=5)
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill='x', pady=10)
        
        ttk.Button(btn_frame, text="👁️ SKONTROLOVAŤ TERAZ", command=self.check_position).pack(side='left', padx=5)
        
        # Monitor výsledok
        result_frame = ttk.LabelFrame(parent, text="Stav pozície", padding=10)
        result_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.monitor_result_text = scrolledtext.ScrolledText(result_frame, height=15, font=('Courier', 11))
        self.monitor_result_text.pack(fill='both', expand=True)
    
    def create_archive_tab(self, parent):
        """Záložka pre archív stratégií s prehľadným zoznamom"""
        # Hlavný frame
        main_frame = ttk.Frame(parent, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Ľavá strana - zoznam stratégií
        left_frame = ttk.LabelFrame(main_frame, text="📋 Uložené stratégie", padding=5)
        left_frame.pack(side='left', fill='both', expand=True, padx=(0, 5))
        
        # Listbox so scrollbarom
        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill='both', expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side='right', fill='y')
        
        self.archive_listbox = tk.Listbox(list_frame, font=('Courier', 11), 
                                          yscrollcommand=scrollbar.set, selectmode='single')
        self.archive_listbox.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=self.archive_listbox.yview)
        
        # Bind double-click pre načítanie
        self.archive_listbox.bind('<Double-1>', lambda e: self.archive_load_selected())
        self.archive_listbox.bind('<<ListboxSelect>>', lambda e: self.archive_show_details())
        
        # Tlačidlá pod zoznamom
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill='x', pady=5)
        
        ttk.Button(btn_frame, text="📂 Načítať", command=self.archive_load_selected, width=12).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="🗑️ Vymazať", command=self.archive_delete_selected, width=12).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="🔄 Obnoviť", command=self.archive_refresh_list, width=12).pack(side='left', padx=2)
        
        # Pravá strana - detaily stratégie
        right_frame = ttk.LabelFrame(main_frame, text="📝 Detaily stratégie", padding=5)
        right_frame.pack(side='right', fill='both', expand=True, padx=(5, 0))
        
        self.archive_details_text = tk.Text(right_frame, font=('Courier', 11), wrap='word', state='disabled')
        self.archive_details_text.pack(fill='both', expand=True)
        
        # Načítaj zoznam pri vytvorení
        self.root.after(100, self.archive_refresh_list)
    
    def archive_refresh_list(self):
        """Obnoví zoznam stratégií v archíve"""
        self.archive_listbox.delete(0, tk.END)
        
        for name in sorted(self.saved_strategies.keys()):
            strategy = self.saved_strategies[name]
            symbol = strategy.get('symbol', '?')
            opt_type = strategy.get('option_type', '?')
            short_strike = strategy.get('short_strike', '?')
            self.archive_listbox.insert(tk.END, f"{name} | {symbol} {opt_type} {short_strike}")
    
    def archive_show_details(self):
        """Zobrazí detaily vybranej stratégie"""
        selection = self.archive_listbox.curselection()
        if not selection:
            return
        
        item = self.archive_listbox.get(selection[0])
        name = item.split(' | ')[0]
        
        if name not in self.saved_strategies:
            return
        
        strategy = self.saved_strategies[name]
        
        details = f"""STRATÉGIA: {name}
{'='*40}

Symbol:           {strategy.get('symbol', '-')}
Typ:              {strategy.get('option_type', '-')}
Podklad:          ${strategy.get('underlying_price', '-')}

SHORT LEG:
  Strike:         {strategy.get('short_strike', '-')}
  Expirácia:      {strategy.get('short_expiry', '-')}
  Premium:        ${strategy.get('short_premium', '-')}

LONG LEG:
  Strike:         {strategy.get('long_strike', '-')}
  Expirácia:      {strategy.get('long_expiry', '-')}
  Premium:        ${strategy.get('long_premium', '-')}

Broker:           {strategy.get('broker', '-')}
Uložená:          {strategy.get('saved_at', '-')}
"""
        
        self.archive_details_text.config(state='normal')
        self.archive_details_text.delete(1.0, tk.END)
        self.archive_details_text.insert(tk.END, details)
        self.archive_details_text.config(state='disabled')
    
    def archive_load_selected(self):
        """Načíta vybranú stratégiu do kalkulátora"""
        selection = self.archive_listbox.curselection()
        if not selection:
            messagebox.showwarning("Chyba", "Vyberte stratégiu zo zoznamu")
            return
        
        item = self.archive_listbox.get(selection[0])
        name = item.split(' | ')[0]
        
        self.strategy_name_var.set(name)
        self.load_strategy()
    
    def archive_delete_selected(self):
        """Vymaže vybranú stratégiu"""
        selection = self.archive_listbox.curselection()
        if not selection:
            messagebox.showwarning("Chyba", "Vyberte stratégiu na vymazanie")
            return
        
        item = self.archive_listbox.get(selection[0])
        name = item.split(' | ')[0]
        
        confirm = messagebox.askyesno("Potvrdenie", f"Naozaj chcete vymazať stratégiu '{name}'?")
        if confirm:
            if name in self.saved_strategies:
                del self.saved_strategies[name]
                self.save_settings_file()
                self.archive_refresh_list()
                
                # Aktualizuj aj combobox v kalkulátore
                strategy_names = sorted(self.saved_strategies.keys())
                self.strategy_combo['values'] = strategy_names
                
                # Vyčisti detaily
                self.archive_details_text.config(state='normal')
                self.archive_details_text.delete(1.0, tk.END)
                self.archive_details_text.config(state='disabled')
                
                messagebox.showinfo("Vymazané", f"Stratégia '{name}' bola vymazaná.")
    
    def create_results_tab(self, parent):
        """Záložka pre kompletné výsledky"""
        frame = ttk.Frame(parent, padding=10)
        frame.pack(fill='both', expand=True)
        
        self.full_results_text = scrolledtext.ScrolledText(frame, font=('Courier', 10))
        self.full_results_text.pack(fill='both', expand=True)
        
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Button(btn_frame, text="💾 Uložiť výsledky", command=self.save_results).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="🗑️ Vymazať", command=self.clear_results).pack(side='left', padx=5)
    
    def create_spread_calculator_tab(self, parent):
        """Záložka pre manuálny Spread Kalkulátor - bez nutnosti market data"""
        
        # === ARCHÍV NASTAVENÍ ===
        archive_frame = ttk.LabelFrame(parent, text="💾 Archív Nastavení", padding=5)
        archive_frame.pack(fill='x', padx=10, pady=5)
        
        archive_row = ttk.Frame(archive_frame)
        archive_row.pack(fill='x', padx=5, pady=5)
        
        ttk.Label(archive_row, text="Stratégia:").pack(side='left', padx=5)
        self.strategy_name_var = tk.StringVar()
        self.strategy_combo = ttk.Combobox(archive_row, textvariable=self.strategy_name_var, width=35)
        self.strategy_combo.pack(side='left', padx=5)
        
        ttk.Button(archive_row, text="💾 Uložiť", command=self.save_strategy, width=10).pack(side='left', padx=2)
        ttk.Button(archive_row, text="📂 Načítať", command=self.load_strategy, width=10).pack(side='left', padx=2)
        ttk.Button(archive_row, text="🧹 Vyčistiť", command=self.delete_strategy, width=10).pack(side='left', padx=2)
        
        # Načítaj uložené stratégie pri štarte
        self.load_settings_file()
        
        # === Vstupné parametre ===
        input_frame = ttk.LabelFrame(parent, text="📝 Zadajte parametre spreadu", padding=10)
        input_frame.pack(fill='x', padx=10, pady=10)
        
        # Riadok 0: Symbol, Typ, Underlying Price
        row0 = ttk.Frame(input_frame)
        row0.pack(fill='x', pady=5)
        
        ttk.Label(row0, text="Symbol:").pack(side='left', padx=5)
        ttk.Entry(row0, textvariable=self.symbol_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row0, text="Typ:").pack(side='left', padx=5)
        ttk.Combobox(row0, textvariable=self.option_type_var, values=["PUT", "CALL"], width=6).pack(side='left', padx=5)
        
        ttk.Label(row0, text="Cena podkladu $:").pack(side='left', padx=5)
        ttk.Entry(row0, textvariable=self.calc_underlying_price_var, width=10).pack(side='left', padx=5)
        
        ttk.Button(row0, text="📥 Stiahnuť cenu", command=self.fetch_underlying_price).pack(side='left', padx=10)
        
        # ATR 14d - button + multiplier
        ttk.Label(row0, text=" ").pack(side='left', padx=4)  # spacer
        ttk.Button(row0, text="📈 ATR14", command=self.fetch_atr, width=10).pack(side='left', padx=2)
        ttk.Label(row0, text="× ATR:").pack(side='left', padx=4)
        self.atr_spin = tk.Spinbox(row0, from_=1.0, to=3.0, increment=0.2, textvariable=self.atr_multiplier_var, width=4, format="%.1f", command=self.update_atr_display)
        self.atr_spin.pack(side='left', padx=2)
        self.atr_label = ttk.Label(row0, text="ATR14: —")
        self.atr_label.pack(side='left', padx=6)
        
        # Riadok 0.5: Stoploss na cenu opcie
        row05 = ttk.Frame(input_frame)
        row05.pack(fill='x', pady=3)
        
        ttk.Label(row05, text="🛑 Stoploss - cena opcie keď podklad dosiahne short strike:").pack(side='left', padx=5)
        self.stoploss_price_label = ttk.Label(row05, text="", foreground="red", font=('TkDefaultFont', 10, 'bold'))
        self.stoploss_price_label.pack(side='left', padx=10)
        ttk.Button(row05, text="🔄 Prepočítať", command=self.calculate_stoploss_price, width=12).pack(side='left', padx=5)
        
        # Riadok 1: SHORT LEG
        short_frame = ttk.LabelFrame(input_frame, text="🔴 SHORT LEG (predávaná opcia)", padding=5)
        short_frame.pack(fill='x', pady=5)
        
        short_row = ttk.Frame(short_frame)
        short_row.pack(fill='x', pady=3)
        
        ttk.Label(short_row, text="Strike:").pack(side='left', padx=5)
        ttk.Entry(short_row, textvariable=self.calc_short_strike_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(short_row, text="Expiry (YYYYMMDD):").pack(side='left', padx=5)
        self.calc_short_expiry_combo = ttk.Combobox(short_row, textvariable=self.calc_short_expiry_var, width=12)
        self.calc_short_expiry_combo.pack(side='left', padx=5)
        
        ttk.Label(short_row, text="Premium $:").pack(side='left', padx=5)
        ttk.Entry(short_row, textvariable=self.calc_short_premium_var, width=8).pack(side='left', padx=5)
        
        ttk.Button(short_row, text="📥 Stiahnuť", command=lambda: self.fetch_option_price('short')).pack(side='left', padx=10)
        
        # Label pre odporúčaný strike
        self.short_rec_strike_label = ttk.Label(short_row, text="", foreground="blue")
        self.short_rec_strike_label.pack(side='left', padx=5)
        
        # Riadok 2: LONG LEG
        long_frame = ttk.LabelFrame(input_frame, text="🟢 LONG LEG (kupovaná opcia)", padding=5)
        long_frame.pack(fill='x', pady=5)
        
        long_row = ttk.Frame(long_frame)
        long_row.pack(fill='x', pady=3)
        
        ttk.Label(long_row, text="Strike:").pack(side='left', padx=5)
        ttk.Entry(long_row, textvariable=self.calc_long_strike_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(long_row, text="Expiry (YYYYMMDD):").pack(side='left', padx=5)
        self.calc_long_expiry_combo = ttk.Combobox(long_row, textvariable=self.calc_long_expiry_var, width=12)
        self.calc_long_expiry_combo.pack(side='left', padx=5)
        
        ttk.Label(long_row, text="Premium $:").pack(side='left', padx=5)
        ttk.Entry(long_row, textvariable=self.calc_long_premium_var, width=8).pack(side='left', padx=5)
        
        ttk.Button(long_row, text="📥 Stiahnuť", command=lambda: self.fetch_option_price('long')).pack(side='left', padx=10)
        
        # Riadok 3: Broker a tlačidlá
        btn_row = ttk.Frame(input_frame)
        btn_row.pack(fill='x', pady=10)
        
        ttk.Label(btn_row, text="Broker:").pack(side='left', padx=5)
        ttk.Combobox(btn_row, textvariable=self.broker_var, values=["IBKR", "SAXO"], width=8).pack(side='left', padx=5)
        
        ttk.Button(btn_row, text="🔄 Načítať expirácie", command=self.load_expiries_for_calc).pack(side='left', padx=10)
        
        ttk.Button(btn_row, text="🧮 VYPOČÍTAŤ", command=self.calculate_spread, 
                   style='Accent.TButton').pack(side='left', padx=20)
        
        # Status label pre kalkulátor
        self.calc_status_label = ttk.Label(btn_row, text="Pripravené")
        self.calc_status_label.pack(side='left', padx=20)
        
        # === Výsledky výpočtu ===
        result_frame = ttk.LabelFrame(parent, text="📊 Výsledky kalkulácie", padding=10)
        result_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.calc_result_text = scrolledtext.ScrolledText(result_frame, height=20, font=('Courier', 10))
        self.calc_result_text.pack(fill='both', expand=True)
    
    def fetch_underlying_price(self):
        """Stiahne aktuálnu cenu podkladového aktíva"""
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_price.py')
                result = subprocess.run(
                    ['python3', script_path, str(self.port_var.get()), self.symbol_var.get()], 
                    capture_output=True, text=True, timeout=20,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                
                output = result.stdout.strip()
                stderr = result.stderr.strip()
                
                # Parse output - first line is price, second is DEBUG
                lines = output.split('\n')
                first_line = lines[0] if lines else ''
                
                if first_line.startswith("ERROR:"):
                    error_msg = first_line.replace("ERROR:", "")
                    self.root.after(0, lambda msg=error_msg: self.update_calc_status(f"❌ {msg}"))
                elif result.returncode == 0 and first_line:
                    try:
                        price = first_line.split('\n')[0]
                        float(price)
                        self.root.after(0, lambda p=price: self.calc_underlying_price_var.set(p))
                        self.root.after(0, lambda p=price, sym=self.symbol_var.get(): self.update_calc_status(f"✓ {sym}: ${p}"))
                        self.root.after(100, self.update_recommended_strike)
                    except ValueError:
                        self.root.after(0, lambda out=first_line: self.update_calc_status(f"❌ Neplatná cena: {out}"))
                elif not output:
                    self.root.after(0, lambda err=stderr[:100]: self.update_calc_status(f"❌ TWS: {err}"))
                else:
                    self.root.after(0, lambda: self.update_calc_status(f"❌ Nepodarilo sa načítať cenu"))
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self.update_calc_status(f"❌ Timeout - TWS neodpovedá"))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self.update_calc_status(f"❌ {err}"))
        
        self.update_calc_status("Sťahujem cenu z TWS...")
        threading.Thread(target=run, daemon=True).start()

    def fetch_option_price(self, leg_type):
        """Stiahne cenu konkrétnej opcie"""
        if leg_type == 'short':
            strike = self.calc_short_strike_var.get()
            expiry = self.calc_short_expiry_var.get()
            premium_var = self.calc_short_premium_var
        else:
            strike = self.calc_long_strike_var.get()
            expiry = self.calc_long_expiry_var.get()
            premium_var = self.calc_long_premium_var

        # Debug - zobraz hodnoty
        print(f"DEBUG fetch_option_price: leg={leg_type}, strike='{strike}', expiry='{expiry}'")

        if not strike or not expiry:
            messagebox.showwarning("Chyba", f"Zadajte strike a expiry\nstrike='{strike}', expiry='{expiry}'")
            return
        
        right = 'C' if self.option_type_var.get() == 'CALL' else 'P'
        symbol = self.symbol_var.get()
        port = self.port_var.get()
        
        self.update_calc_status(f"Sťahujem {leg_type} {strike}...")
        
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_option.py')
                result = subprocess.run(
                    ['python3', script_path, str(port), symbol, expiry, str(strike), right], 
                    capture_output=True, text=True, timeout=20,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                
                output = result.stdout.strip()
                stderr = result.stderr.strip()
                
                if output.startswith("ERROR:"):
                    error_msg = output.replace("ERROR:", "")
                    self.root.after(0, lambda msg=error_msg, lt=leg_type: self.update_calc_status(f"❌ {lt}: {msg}"))
                    self.root.after(0, lambda msg=error_msg, lt=leg_type: messagebox.showwarning("Chyba", 
                        f"Nepodarilo sa stiahnuť cenu pre {lt}.\n\n{msg}\n\nZadajte premium manuálne."))
                elif result.returncode == 0 and output:
                    try:
                        price = float(output)
                        if price > 0:
                            self.root.after(0, lambda pvar=premium_var, val=output: pvar.set(val))
                            self.root.after(0, lambda lt=leg_type, st=strike, val=output: self.update_calc_status(
                                f"✓ {lt.upper()} {st} @ ${val}"))
                            # Aktualizuj stoploss label ak je SHORT
                            if leg_type == 'short':
                                self.root.after(100, self.update_stoploss_label)
                        else:
                            self.root.after(0, lambda lt=leg_type: self.update_calc_status(
                                f"❌ {lt}: Cena = 0, zadajte manuálne"))
                    except ValueError:
                        self.root.after(0, lambda out=output: self.update_calc_status(
                            f"❌ Neplatná odpoveď: {out}"))
                elif not output:
                    self.root.after(0, lambda err=stderr[:100]: self.update_calc_status(f"❌ TWS: {err}"))
                else:
                    self.root.after(0, lambda: self.update_calc_status(f"❌ Nepodarilo sa načítať premium"))
                        
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self.update_calc_status(f"❌ Timeout - TWS neodpovedá"))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self.update_calc_status(f"❌ {err}"))
        
        threading.Thread(target=run, daemon=True).start()

    def fetch_atr(self):
        """Stiahne 7-denný priemer rozsahu (high-low) cez TWS; ak zlyhá, skúsi yfinance ako fallback"""
        def run():
            symbol = self.symbol_var.get()
            port = int(self.port_var.get() or 7496)
            self.root.after(0, lambda: self.update_calc_status(f"Sťahujem 7d range pre {symbol}..."))
            
            try:
                # Skús TWS cez externý script
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_atr.py')
                result = subprocess.run(
                    ['python3', script_path, str(port), symbol],
                    capture_output=True, text=True, timeout=15,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                
                output = result.stdout.strip()
                
                if result.returncode == 0 and output and not output.startswith("ERROR:"):
                    avg = float(output)
                    from datetime import datetime
                    updated = datetime.now().strftime('%Y-%m-%d %H:%M')
                    self.atr_7d = avg
                    self.atr_last_updated = updated
                    mult = self.atr_multiplier_var.get()
                    self.root.after(0, lambda: self.atr_label.config(text=f"ATR14: ${avg:.2f} | {mult}x=${avg*mult:.2f}"))
                    self.root.after(0, lambda: self.update_calc_status(f"✓ ATR14 ${avg:.2f} (TWS)"))
                    self.root.after(100, self.update_recommended_strike)
                    return
                else:
                    raise RuntimeError(output if output else "TWS failed")
                    
            except Exception as e:
                # Fallback to yfinance
                try:
                    import yfinance as yf
                    df = yf.download(symbol, period='21d', interval='1d', progress=False)
                    if df is None or df.empty or len(df) < 14:
                        raise RuntimeError('Nedostatočné dáta z yfinance')
                    prices = df['High'] - df['Low']
                    avg = float(prices[-14:].mean())
                    from datetime import datetime
                    updated = datetime.now().strftime('%Y-%m-%d %H:%M')
                    self.atr_7d = avg
                    self.atr_last_updated = updated
                    mult = self.atr_multiplier_var.get()
                    self.root.after(0, lambda: self.atr_label.config(text=f"ATR14: ${avg:.2f} | {mult}x=${avg*mult:.2f}"))
                    self.root.after(0, lambda: self.update_calc_status(f"✓ ATR14 ${avg:.2f} (yfinance)"))
                    self.root.after(100, self.update_recommended_strike)
                except Exception as e2:
                    self.root.after(0, lambda: self.update_calc_status(f"❌ ATR: {e2}"))
        
        threading.Thread(target=run, daemon=True).start()
    
    def update_atr_display(self):
        """Aktualizuje ATR label pri zmene multipliera"""
        if hasattr(self, 'atr_7d') and self.atr_7d and self.atr_7d > 0:
            avg = self.atr_7d
            mult = self.atr_multiplier_var.get()
            self.atr_label.config(text=f"ATR14: ${avg:.2f} | {mult}x=${avg*mult:.2f}")
        self.update_recommended_strike()
    
    def update_recommended_strike(self):
        """Aktualizuje odporúčaný strike pre SHORT (podklad +/- ATR)"""
        try:
            underlying = float(self.calc_underlying_price_var.get() or 0)
            atr = getattr(self, 'atr_7d', 0) or 0
            mult = self.atr_multiplier_var.get()
            option_type = self.option_type_var.get()
            
            if underlying > 0 and atr > 0:
                if option_type == 'CALL':
                    rec_strike = underlying + (atr * mult)
                else:  # PUT
                    rec_strike = underlying - (atr * mult)
                
                # Zaokrúhli na najbližší strike (typicky 1 alebo 5)
                if underlying > 100:
                    rec_strike = round(rec_strike)  # zaokrúhli na celé číslo
                else:
                    rec_strike = round(rec_strike, 1)  # zaokrúhli na 1 desatinné
                
                self.short_rec_strike_label.config(text=f"💡 Odpor.: {rec_strike}")
            else:
                self.short_rec_strike_label.config(text="")
        except Exception as e:
            print(f"DEBUG update_recommended_strike error: {e}")
            self.short_rec_strike_label.config(text="")
    
    def update_stoploss_label(self):
        """Deprecated - use calculate_stoploss_price"""
        pass
    
    def calculate_stoploss_price(self, silent=False):
        """Vypočíta cenu SHORT opcie keď podklad dosiahne short strike (ATM)"""
        try:
            short_strike = float(self.calc_short_strike_var.get() or 0)
            short_expiry = self.calc_short_expiry_var.get()
            short_premium = float(self.calc_short_premium_var.get() or 0)
            underlying = float(self.calc_underlying_price_var.get() or 0)
            iv = float(self.iv_var.get() or 0.20)
            
            if not short_strike or not short_expiry:
                if not silent:
                    messagebox.showwarning("Chyba", "Zadajte short strike a expiry")
                self.stoploss_price_label.config(text="")
                return
            
            # Výpočet DTE
            from datetime import datetime
            today = datetime.now()
            try:
                exp_date = datetime.strptime(short_expiry, '%Y%m%d')
                dte = max(1, (exp_date - today).days)
            except:
                self.stoploss_price_label.config(text="")
                return
            
            # ATM opcia aproximácia (Brenner-Subrahmanyam formula)
            # ATM cena ≈ 0.4 × S × σ × √(T)
            # kde S = strike (keď je ATM), σ = IV, T = čas v rokoch
            import math
            t_years = dte / 365.0
            atm_price = 0.4 * short_strike * iv * math.sqrt(t_years)
            
            # Uložíme pre zobrazenie
            self.calculated_stoploss = atm_price
            
            # Zobraz výsledok
            self.stoploss_price_label.config(
                text=f"🛑 STOPLOSS: ${atm_price:.2f} (opcia pri strike ${short_strike:.0f}, DTE {dte}, IV {iv:.0%})"
            )
            
            if not silent:
                self.update_calc_status(f"✓ Stoploss vypočítaný: ${atm_price:.2f}")
            
        except Exception as e:
            if not silent:
                messagebox.showerror("Chyba", f"Nepodarilo sa vypočítať stoploss:\n{e}")
    
    def auto_recalc_stoploss(self):
        """Automaticky prepočíta stoploss pri zmene strike, expiry alebo IV"""
        # Oneskorené volanie aby sa predišlo príliš častému prepočítavaniu
        if hasattr(self, '_stoploss_after_id'):
            self.root.after_cancel(self._stoploss_after_id)
        self._stoploss_after_id = self.root.after(300, lambda: self.calculate_stoploss_price(silent=True))
    
    def load_expiries_for_calc(self):
        """Načíta expirácie pre kalkulátor"""
        self.load_expiries()
    
    def update_calc_expiry_combos(self):
        """Aktualizuje combobox expiracií v kalkulátore - už nie je potrebná"""
        pass
    
    def calculate_spread(self):
        """Vypočíta parametre spreadu"""
        try:
            # Získaj hodnoty
            short_strike = float(self.calc_short_strike_var.get() or 0)
            short_premium = float(self.calc_short_premium_var.get() or 0)
            short_expiry = self.calc_short_expiry_var.get()
            
            long_strike = float(self.calc_long_strike_var.get() or 0)
            long_premium = float(self.calc_long_premium_var.get() or 0)
            long_expiry = self.calc_long_expiry_var.get()
            
            underlying_price = float(self.calc_underlying_price_var.get() or 0)
            
            option_type = self.option_type_var.get()
            broker = self.broker_var.get()
            
            # Validácia
            if not all([short_strike, short_premium, underlying_price]):
                messagebox.showwarning("Chyba", "Vyplňte všetky povinné polia (strike, premium, cena podkladu)")
                return
            
            # Základné výpočty
            spread_width = abs(short_strike - long_strike) if long_strike > 0 else 0
            same_expiry = (short_expiry == long_expiry) or not long_expiry
            
            # DTE výpočet
            from datetime import datetime
            today = datetime.now()
            if short_expiry:
                short_exp_date = datetime.strptime(short_expiry, '%Y%m%d')
                short_dte = max(1, (short_exp_date - today).days)
            else:
                short_dte = 7
            
            if long_expiry:
                long_exp_date = datetime.strptime(long_expiry, '%Y%m%d')
                long_dte = max(1, (long_exp_date - today).days)
            else:
                long_dte = short_dte
            
            # === URČENIE TYPU SPREADU ===
            net_amount = short_premium - long_premium
            is_credit = net_amount > 0
            
            # Určenie typu spreadu podľa strikes a expirácií
            if long_strike == 0 or long_premium == 0:
                # Len short leg - naked option
                spread_type = f"Naked {option_type}"
                is_credit = True
                net_amount = short_premium
            elif same_expiry:
                # Vertical spread (rovnaká expirácia)
                if spread_width == 0:
                    spread_type = f"Single {option_type}"
                elif is_credit:
                    spread_type = f"Vertical CREDIT Spread ({option_type})"
                else:
                    spread_type = f"Vertical DEBIT Spread ({option_type})"
            else:
                # Rôzne expirácie
                if spread_width == 0:
                    # Rovnaký strike, rôzna expirácia = CALENDAR SPREAD
                    if is_credit:
                        spread_type = f"Calendar CREDIT Spread ({option_type})"
                    else:
                        spread_type = f"Calendar DEBIT Spread ({option_type})"
                else:
                    # Rôzny strike aj expirácia = DIAGONAL
                    if is_credit:
                        spread_type = f"Diagonal CREDIT Spread"
                    else:
                        if option_type == 'CALL':
                            spread_type = "PMCC (Poor Man's Covered Call)"
                        else:
                            spread_type = "PMCP (Poor Man's Covered Put)"
            
            # === VÝPOČTY PODĽA TYPU ===
            if is_credit:
                # CREDIT SPREAD - dostávame peniaze
                net_credit = net_amount
                net_debit = 0
                investment = 0  # Pre credit spread nepotrebujeme investíciu
                additional_margin = 0
                total_capital = 0
                
                max_profit = net_credit * 100
                if spread_width > 0:
                    max_loss = (spread_width - net_credit) * 100
                else:
                    # Naked - teoreticky neobmedzená strata
                    max_loss = float('inf')
                
                # Break-even pre CREDIT
                if option_type == 'PUT':
                    break_even = short_strike - net_credit
                else:
                    break_even = short_strike + net_credit
                
                # Margin pre CREDIT spread
                broker_pct = 0.10 if broker == 'IBKR' else 0.15
                if spread_width > 0 and same_expiry:
                    margin = spread_width * 100
                elif spread_width > 0:
                    # Diagonal credit
                    margin = spread_width * 100 * 1.2
                else:
                    # Naked
                    margin = underlying_price * broker_pct * 100
                
                # ROI pre CREDIT
                if margin > 0:
                    total_roi = (net_credit * 100 / margin) * 100
                    weekly_roi = total_roi / short_dte * 7
                    annual_roi = weekly_roi * 52
                else:
                    total_roi = weekly_roi = annual_roi = 0
                
                # Roll trigger pre CREDIT (pri 30% strate)
                roll_trigger_loss = net_credit * 0.30
                if option_type == 'PUT':
                    roll_trigger_price = short_strike + roll_trigger_loss
                else:
                    roll_trigger_price = short_strike - roll_trigger_loss
                    
            else:
                # DEBIT SPREAD - platíme peniaze
                net_credit = 0
                net_debit = abs(net_amount)
                
                max_loss = net_debit * 100  # Maximálna strata = to čo sme zaplatili
                
                if same_expiry and spread_width > 0:
                    # Vertical debit - obmedzený profit
                    max_profit = (spread_width - net_debit) * 100
                    max_profit_str_note = "obmedzený"
                    # Margin pre vertical debit = net debit (žiadny dodatočný margin)
                    additional_margin = 0
                elif spread_width == 0:
                    # CALENDAR SPREAD - rovnaký strike, rôzne expirácie
                    # Long leg kryje short leg, margin je minimálny
                    max_profit_at_short_exp = short_premium * 100
                    max_profit = float('inf')  # Teoreticky neobmedzený ak long leg rastie
                    max_profit_str_note = f"${max_profit_at_short_exp:.0f} pri exp short"
                    # Calendar spread margin:
                    # IBKR: Typicky malý margin (~15-20% z net debit alebo rozdiel v theta)
                    # Saxo: Často $0 (long kryje short úplne)
                    if broker == 'IBKR':
                        # IBKR požaduje cca 15-20% z hodnoty long leg ako margin
                        additional_margin = long_premium * 100 * 0.15  # ~15% z long premium
                    else:
                        # Saxo - $0 dodatočný margin pre calendar spread
                        additional_margin = 0
                else:
                    # DIAGONAL SPREAD (PMCC/PMCP) - rôzny strike aj expirácia
                    # Max profit = short premium (ak expiruje bezcenne) + potenciál long leg
                    max_profit_at_short_exp = short_premium * 100  # Profit ak short expiruje OTM
                    max_profit = float('inf')  # Teoreticky neobmedzený
                    max_profit_str_note = f"${max_profit_at_short_exp:.0f} pri exp short + potenciál long"
                    # Pre PMCC/PMCP broker vyžaduje dodatočný margin
                    # IBKR: Typicky margin ako pre spread
                    # Saxo: Tiež vyžaduje margin
                    if broker == 'IBKR':
                        # IBKR margin pre diagonal: rozdiel strikes + časová hodnota
                        additional_margin = max(spread_width * 100, underlying_price * 0.05 * 100)
                    else:
                        # Saxo margin pre diagonal
                        additional_margin = spread_width * 100 * 1.5
                
                # Break-even pre DEBIT
                if option_type == 'PUT':
                    # Put debit spread - potrebujete pokles ceny
                    break_even = short_strike + net_debit
                else:
                    # Call debit spread / PMCC - potrebujete rast ceny
                    break_even = short_strike + net_debit
                
                # Investment = Net Debit (čo zaplatíme)
                investment = net_debit * 100
                
                # Total capital = Investment + Additional Margin
                total_capital = investment + additional_margin
                
                # Pre zobrazenie použijeme "margin" ako celkový kapitálový požiadavek
                margin = total_capital
                
                # ROI pre DEBIT spread - počítame z celkového kapitálu
                if total_capital > 0:
                    if max_profit != float('inf'):
                        # Vertical debit - klasický ROI
                        total_roi = (max_profit / total_capital) * 100
                    else:
                        # PMCC/PMCP - ROI ak short leg expiruje OTM (dostaneme short premium)
                        # Počítame ROI z short premium vs celkový kapitál
                        short_profit = short_premium * 100
                        total_roi = (short_profit / total_capital) * 100
                    weekly_roi = total_roi / short_dte * 7
                    annual_roi = weekly_roi * 52
                else:
                    total_roi = weekly_roi = annual_roi = 0
                
                # Roll/Exit trigger pre DEBIT (ak short leg je ITM)
                if option_type == 'CALL':
                    roll_trigger_price = short_strike  # Ak cena presiahne short strike
                else:
                    roll_trigger_price = short_strike  # Ak cena klesne pod short strike
            
            # === VÝSTUP ===
            if is_credit:
                credit_debit_label = "Net CREDIT"
                credit_debit_value = f"${net_credit:.2f}"
                credit_debit_total = f"${net_credit*100:.2f}"
                roi_note = "ROI = (Credit / Margin) - zarábate na time decay"
                margin_section = f"║  💼 MARGIN ({broker}):  ${margin:,.2f}                                   ║"
            else:
                credit_debit_label = "Net DEBIT"
                credit_debit_value = f"${net_debit:.2f}"
                credit_debit_total = f"-${net_debit*100:.2f}"
                roi_note = f"ROI = (Short Premium / Celkový kapitál) × 100"
                # Detailný rozpis nákladov pre DEBIT spread
                if not same_expiry:
                    # Calendar alebo Diagonal spread - ukáž rozpis
                    if additional_margin > 0:
                        margin_section = f"""║  💼 NÁKLADY ({broker}):                                              ║
║     Investment (Net Debit):   ${investment:,.2f}                        ║
║     Dodatočný Margin:         ${additional_margin:,.2f}                        ║
║     ────────────────────────────────────                         ║
║     CELKOVÝ KAPITÁL:          ${total_capital:,.2f}                        ║"""
                    else:
                        margin_section = f"""║  💼 NÁKLADY ({broker}):                                              ║
║     Investment (Net Debit):   ${investment:,.2f}                        ║
║     Dodatočný Margin:         $0.00 (long kryje short)              ║
║     ────────────────────────────────────                         ║
║     CELKOVÝ KAPITÁL:          ${total_capital:,.2f}                        ║"""
                else:
                    margin_section = f"║  💼 INVESTMENT ({broker}):  ${margin:,.2f}                              ║"
            
            max_profit_str = f"${max_profit:,.2f}" if max_profit != float('inf') else "NEOBMEDZENÝ ↑"
            max_loss_str = f"${max_loss:,.2f}" if max_loss != float('inf') else "NEOBMEDZENÁ ↓"
            
            result = f"""
╔══════════════════════════════════════════════════════════════════╗
║                    📊 SPREAD KALKULÁCIA                          ║
╠══════════════════════════════════════════════════════════════════╣
║  Symbol: {self.symbol_var.get():10}    Typ: {option_type:6}    Broker: {broker:6}     ║
║  Cena podkladu: ${underlying_price:,.2f}                                   ║
╠══════════════════════════════════════════════════════════════════╣
║  🔴 SHORT LEG (predávate):                                       ║
║     Strike: ${short_strike:,.2f}    Premium: ${short_premium:.2f}    DTE: {short_dte:3}        ║
║     Expiry: {short_expiry or 'N/A':10}                                       ║
║                                                                  ║
║  🟢 LONG LEG (kupujete):                                         ║
║     Strike: ${long_strike:,.2f}    Premium: ${long_premium:.2f}    DTE: {long_dte:3}        ║
║     Expiry: {long_expiry or 'N/A':10}                                       ║
╠══════════════════════════════════════════════════════════════════╣
║  📐 TYP SPREADU: {spread_type:45}║
║  📏 Šírka spreadu: ${spread_width:,.2f}                                    ║
╠══════════════════════════════════════════════════════════════════╣
║                      💰 VÝPOČTY                                  ║
╠══════════════════════════════════════════════════════════════════╣
║  {credit_debit_label}:     {credit_debit_value} per share ({credit_debit_total} per contract) ║
║  Max Profit:      {max_profit_str:20}                         ║
║  Max Loss:        {max_loss_str:20}                          ║
║  Break-Even:      ${break_even:,.2f}                                       ║
╠══════════════════════════════════════════════════════════════════╣
{margin_section}
╠══════════════════════════════════════════════════════════════════╣
║  📈 ROI ANALÝZA:                                                 ║
║     Total ROI:    {total_roi:6.2f}% (za {short_dte} dní)                       ║
║     Weekly ROI:   {weekly_roi:6.2f}%                                        ║
║     Annual ROI:   {annual_roi:6.2f}% (projected)                           ║
╠══════════════════════════════════════════════════════════════════╣
║  ⚠️  MANAGEMENT (Roll ak):                                       ║
║     Cena podkladu dosiahne short strike: ${short_strike:,.2f}              ║
║     ➡️  Stoploss na cenu opcie: klikni "Prepočítať" vyššie        ║
╚══════════════════════════════════════════════════════════════════╝

📝 POZNÁMKY:
• {credit_debit_label} = Short Premium (${short_premium:.2f}) - Long Premium (${long_premium:.2f})
• {roi_note}
• Hodnoty sú per 1 kontrakt (100 shares)
"""
            
            # Pridaj poznámky podľa typu spreadu
            if not is_credit:
                if not same_expiry and spread_width != 0:
                    # PMCC/PMCP diagonal
                    result += f"""
📋 DIAGONAL DEBIT SPREAD (PMCC/PMCP):
• Net Debit (investícia): ${investment:.2f}
• Margin (short leg):     ${additional_margin:.2f}
• CELKOVÝ KAPITÁL:        ${total_capital:.2f}
• ROI = ${short_premium*100:.2f} / ${total_capital:.2f} × 100 = {total_roi:.2f}%
• Ak short exp OTM: predajte ďalší short, znížte cost basis
• Ak short ITM: roll short alebo close pozíciu
• Break-even: cena musí byť {'nad' if option_type == 'CALL' else 'pod'} ${break_even:.2f}
"""
                elif not same_expiry:
                    # Calendar spread
                    result += f"""
📋 CALENDAR DEBIT SPREAD:
• Investícia: ${net_debit*100:.2f} (net debit)
• ROI ak short expiruje OTM: {total_roi:.2f}%
• Profitujete z time decay short leg
"""
                else:
                    # Vertical debit
                    result += f"""
📋 VERTICAL DEBIT SPREAD:
• Investícia: ${net_debit*100:.2f} (max strata)
• Max profit: ${max_profit:.2f} ak cena je {'nad' if option_type == 'CALL' else 'pod'} ${long_strike:.2f}
• Break-even: ${break_even:.2f}
"""
            else:
                # Credit spread
                import math
                t_years = short_dte / 365.0
                iv = float(self.iv_var.get() or 0.20)
                atm_price = 0.4 * short_strike * iv * math.sqrt(t_years)
                result += f"""
📋 CREDIT SPREAD:
• Prijatý kredit: ${net_credit*100:.2f}
• Max strata: ${max_loss:.2f if max_loss != float('inf') else 'NEOBMEDZENÁ'}
• Cieľ: short leg expiruje OTM, ponecháte celý kredit

🛑 STOPLOSS (keď podklad dosiahne short strike ${short_strike:.0f}):
• Odhadovaná cena SHORT opcie pri ATM: ${atm_price:.2f}
• Nastav STOPLOSS order na opciu: BUY @ ${atm_price:.2f} (alebo market)
• IV použitá: {iv:.0%}, DTE: {short_dte}
"""
            
            # Pridaj varovanie podľa ATR (iba ak je ATR stiahnutá)
            try:
                if getattr(self, 'atr_7d', None) and self.atr_7d > 0:
                    atr = self.atr_7d
                    mult = float(self.atr_multiplier_var.get() or 1.0)
                    distance = abs(short_strike - underlying_price)
                    if distance <= mult * atr:
                        result += f"\n⚠️ VAROVANIE: Strike je v rámci {mult:.1f}×ATR (≤ ${mult*atr:.2f}) - zvážte väčšiu vzdialenosť pre DTE-5.\n"
            except Exception:
                pass

            self.calc_result_text.delete(1.0, tk.END)
            self.calc_result_text.insert(tk.END, result)
            
            # Ulož výsledok
            self.last_calc_result = {
                'shortStrike': short_strike,
                'shortPremium': short_premium,
                'shortExpiry': short_expiry,
                'shortDTE': short_dte,
                'longStrike': long_strike,
                'longPremium': long_premium,
                'longExpiry': long_expiry,
                'longDTE': long_dte,
                'netCredit': net_credit if is_credit else -net_debit,
                'isCredit': is_credit,
                'margin': margin,
                'maxProfit': max_profit,
                'maxLoss': max_loss,
                'breakEven': break_even,
                'weeklyROI': weekly_roi,
                'underlyingPrice': underlying_price,
                'optionType': option_type,
                'spreadType': spread_type,
            }
            
        except ValueError as e:
            messagebox.showerror("Chyba", f"Neplatné hodnoty: {e}")
        except Exception as e:
            messagebox.showerror("Chyba", f"Chyba výpočtu: {e}")
    
    def create_margin_optimizer_tab(self, parent):
        """Záložka pre Margin Optimizer - optimalizácia margin/ROI"""
        # === Parametre ===
        params_frame = ttk.LabelFrame(parent, text="Parametre optimalizácie", padding=10)
        params_frame.pack(fill='x', padx=10, pady=10)
        
        # Riadok 1: Symbol, Premium, Typ
        row1 = ttk.Frame(params_frame)
        row1.pack(fill='x', pady=5)
        
        ttk.Label(row1, text="Symbol:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.symbol_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Min Premium $:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.min_premium_var, width=8).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Typ:").pack(side='left', padx=5)
        ttk.Combobox(row1, textvariable=self.option_type_var, values=["PUT", "CALL"], width=6).pack(side='left', padx=5)
        
        # Riadok 2: Broker, Max Margin, Min ROI
        row2 = ttk.Frame(params_frame)
        row2.pack(fill='x', pady=5)
        
        ttk.Label(row2, text="Broker:").pack(side='left', padx=5)
        ttk.Combobox(row2, textvariable=self.broker_var, values=["IBKR", "SAXO"], width=8).pack(side='left', padx=5)
        
        ttk.Label(row2, text="Max Margin $:").pack(side='left', padx=5)
        ttk.Entry(row2, textvariable=self.max_margin_var, width=8).pack(side='left', padx=5)
        
        ttk.Label(row2, text="Min Weekly ROI %:").pack(side='left', padx=5)
        ttk.Entry(row2, textvariable=self.min_roi_var, width=6).pack(side='left', padx=5)
        
        # Riadok 3: DTE Offsets, Expirácie
        row3 = ttk.Frame(params_frame)
        row3.pack(fill='x', pady=5)
        
        ttk.Label(row3, text="DTE Offsets:").pack(side='left', padx=5)
        ttk.Entry(row3, textvariable=self.dte_offsets_var, width=20).pack(side='left', padx=5)
        
        ttk.Label(row3, text="Short Expiry:").pack(side='left', padx=5)
        self.opt_short_expiry_combo = ttk.Combobox(row3, textvariable=self.short_expiry_var, width=12)
        self.opt_short_expiry_combo.pack(side='left', padx=5)
        
        ttk.Button(row3, text="🔄 Načítať expirácie", command=self.load_expiries).pack(side='left', padx=10)
        
        # Tlačidlá
        btn_frame = ttk.Frame(params_frame)
        btn_frame.pack(fill='x', pady=10)
        
        self.optimize_btn = ttk.Button(btn_frame, text="🔍 OPTIMALIZOVAŤ", command=self.run_optimization)
        self.optimize_btn.pack(side='left', padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="🛑 STOP", command=self.stop_optimization, state='disabled')
        self.stop_btn.pack(side='left', padx=5)
        
        self.opt_status_label = ttk.Label(btn_frame, text="Pripravené")
        self.opt_status_label.pack(side='left', padx=20)
        
        ttk.Button(btn_frame, text="📁 Export Excel", command=self.export_results).pack(side='right', padx=5)
        
        # === Progress bar ===
        progress_frame = ttk.Frame(params_frame)
        progress_frame.pack(fill='x', pady=5)
        
        self.opt_progress = ttk.Progressbar(progress_frame, mode='indeterminate', length=300)
        self.opt_progress.pack(side='left', padx=5)
        
        # === Live log ===
        log_frame = ttk.LabelFrame(parent, text="📋 Priebeh hľadania", padding=5)
        log_frame.pack(fill='x', padx=10, pady=5)
        
        self.opt_log_text = scrolledtext.ScrolledText(log_frame, height=6, font=('Courier', 9))
        self.opt_log_text.pack(fill='x')
        
        # === Tabuľka alternatív ===
        table_frame = ttk.LabelFrame(parent, text="Alternatívy (zoradené podľa Theta-Adjusted ROI)", padding=10)
        table_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Treeview pre alternatívy
        columns = ('dte_offset', 'long_strike', 'margin', 'net_credit', 'weekly_roi', 'theta_adj_roi', 'spread_type')
        self.alt_tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=8)
        
        self.alt_tree.heading('dte_offset', text='DTE Offset')
        self.alt_tree.heading('long_strike', text='Long Strike')
        self.alt_tree.heading('margin', text='Margin $')
        self.alt_tree.heading('net_credit', text='Net Credit')
        self.alt_tree.heading('weekly_roi', text='Weekly ROI %')
        self.alt_tree.heading('theta_adj_roi', text='Theta Adj ROI %')
        self.alt_tree.heading('spread_type', text='Typ')
        
        self.alt_tree.column('dte_offset', width=80, anchor='center')
        self.alt_tree.column('long_strike', width=90, anchor='center')
        self.alt_tree.column('margin', width=90, anchor='center')
        self.alt_tree.column('net_credit', width=90, anchor='center')
        self.alt_tree.column('weekly_roi', width=100, anchor='center')
        self.alt_tree.column('theta_adj_roi', width=110, anchor='center')
        self.alt_tree.column('spread_type', width=80, anchor='center')
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.alt_tree.yview)
        self.alt_tree.configure(yscrollcommand=scrollbar.set)
        
        self.alt_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Bind pre výber riadku
        self.alt_tree.bind('<<TreeviewSelect>>', self.on_alternative_select)
        
        # === Sumárny panel ===
        summary_frame = ttk.LabelFrame(parent, text="Sumár najlepších stratégií", padding=10)
        summary_frame.pack(fill='x', padx=10, pady=10)
        
        self.summary_text = tk.Text(summary_frame, height=4, font=('Courier', 10))
        self.summary_text.pack(fill='x')
    
    def create_interactive_optimizer_tab(self, parent):
        """Záložka pre interaktívnu optimalizáciu - tlačidlá +/- pre strike a expiry"""
        
        # === Aktuálna stratégia (z kalkulátora) ===
        current_frame = ttk.LabelFrame(parent, text="📋 Aktuálna stratégia (z Kalkulátora)", padding=10)
        current_frame.pack(fill='x', padx=10, pady=5)
        
        self.opt_current_label = ttk.Label(current_frame, text="Najprv vypočítajte stratégiu v Kalkulátore", 
                                           font=('Courier', 10))
        self.opt_current_label.pack(fill='x')
        
        ttk.Button(current_frame, text="🔄 Načítať z Kalkulátora", 
                   command=self.load_from_calculator).pack(pady=5)
        
        # === Optimalizačné ovládače ===
        controls_frame = ttk.LabelFrame(parent, text="🎛️ Úprava parametrov", padding=10)
        controls_frame.pack(fill='x', padx=10, pady=5)
        
        # SHORT LEG ovládače
        short_frame = ttk.LabelFrame(controls_frame, text="🔴 SHORT LEG", padding=5)
        short_frame.pack(fill='x', pady=5)
        
        short_row1 = ttk.Frame(short_frame)
        short_row1.pack(fill='x', pady=3)
        
        ttk.Label(short_row1, text="Strike:").pack(side='left', padx=5)
        ttk.Button(short_row1, text="-5", width=4, command=lambda: self.adjust_strike('short', -5)).pack(side='left', padx=2)
        ttk.Button(short_row1, text="-1", width=4, command=lambda: self.adjust_strike('short', -1)).pack(side='left', padx=2)
        self.opt_short_strike_label = ttk.Label(short_row1, text="$---", width=10, font=('Courier', 11, 'bold'))
        self.opt_short_strike_label.pack(side='left', padx=10)
        ttk.Button(short_row1, text="+1", width=4, command=lambda: self.adjust_strike('short', 1)).pack(side='left', padx=2)
        ttk.Button(short_row1, text="+5", width=4, command=lambda: self.adjust_strike('short', 5)).pack(side='left', padx=2)
        
        short_row2 = ttk.Frame(short_frame)
        short_row2.pack(fill='x', pady=3)
        
        ttk.Label(short_row2, text="Expiry:").pack(side='left', padx=5)
        ttk.Button(short_row2, text="◀ Prev", width=8, command=lambda: self.adjust_expiry('short', -1)).pack(side='left', padx=2)
        self.opt_short_expiry_label = ttk.Label(short_row2, text="--------", width=12, font=('Courier', 11, 'bold'))
        self.opt_short_expiry_label.pack(side='left', padx=10)
        ttk.Button(short_row2, text="Next ▶", width=8, command=lambda: self.adjust_expiry('short', 1)).pack(side='left', padx=2)
        
        ttk.Label(short_row2, text="Premium:").pack(side='left', padx=15)
        self.opt_short_premium_entry = ttk.Entry(short_row2, width=8)
        self.opt_short_premium_entry.pack(side='left', padx=2)
        ttk.Button(short_row2, text="📥", width=3, command=lambda: self.fetch_premium('short')).pack(side='left', padx=2)
        
        # LONG LEG ovládače
        long_frame = ttk.LabelFrame(controls_frame, text="🟢 LONG LEG", padding=5)
        long_frame.pack(fill='x', pady=5)
        
        long_row1 = ttk.Frame(long_frame)
        long_row1.pack(fill='x', pady=3)
        
        ttk.Label(long_row1, text="Strike:").pack(side='left', padx=5)
        ttk.Button(long_row1, text="-5", width=4, command=lambda: self.adjust_strike('long', -5)).pack(side='left', padx=2)
        ttk.Button(long_row1, text="-1", width=4, command=lambda: self.adjust_strike('long', -1)).pack(side='left', padx=2)
        self.opt_long_strike_label = ttk.Label(long_row1, text="$---", width=10, font=('Courier', 11, 'bold'))
        self.opt_long_strike_label.pack(side='left', padx=10)
        ttk.Button(long_row1, text="+1", width=4, command=lambda: self.adjust_strike('long', 1)).pack(side='left', padx=2)
        ttk.Button(long_row1, text="+5", width=4, command=lambda: self.adjust_strike('long', 5)).pack(side='left', padx=2)
        
        long_row2 = ttk.Frame(long_frame)
        long_row2.pack(fill='x', pady=3)
        
        ttk.Label(long_row2, text="Expiry:").pack(side='left', padx=5)
        ttk.Button(long_row2, text="◀ Prev", width=8, command=lambda: self.adjust_expiry('long', -1)).pack(side='left', padx=2)
        self.opt_long_expiry_label = ttk.Label(long_row2, text="--------", width=12, font=('Courier', 11, 'bold'))
        self.opt_long_expiry_label.pack(side='left', padx=10)
        ttk.Button(long_row2, text="Next ▶", width=8, command=lambda: self.adjust_expiry('long', 1)).pack(side='left', padx=2)
        
        ttk.Label(long_row2, text="Premium:").pack(side='left', padx=15)
        self.opt_long_premium_entry = ttk.Entry(long_row2, width=8)
        self.opt_long_premium_entry.pack(side='left', padx=2)
        ttk.Button(long_row2, text="📥", width=3, command=lambda: self.fetch_premium('long')).pack(side='left', padx=2)
        
        # Tlačidlo prepočítať
        btn_frame = ttk.Frame(controls_frame)
        btn_frame.pack(fill='x', pady=10)
        
        ttk.Button(btn_frame, text="🔄 Načítať expirácie", command=self.load_expiries).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="🧮 PREPOČÍTAŤ", command=self.recalculate_optimizer).pack(side='left', padx=20)
        ttk.Button(btn_frame, text="📋 Použiť v Kalkulátore", command=self.apply_to_calculator).pack(side='right', padx=5)
        
        # === Porovnanie ===
        compare_frame = ttk.LabelFrame(parent, text="📊 Porovnanie (Pôvodná vs Upravená)", padding=10)
        compare_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        self.opt_compare_text = scrolledtext.ScrolledText(compare_frame, height=15, font=('Courier', 9))
        self.opt_compare_text.pack(fill='both', expand=True)
        
        # Inicializuj optimizer dáta
        self.opt_data = {
            'short_strike': 0,
            'short_expiry': '',
            'short_expiry_idx': 0,
            'short_premium': 0,
            'long_strike': 0,
            'long_expiry': '',
            'long_expiry_idx': 0,
            'long_premium': 0,
            'underlying_price': 0,
            'option_type': 'CALL',
            'original': None  # Pôvodná stratégia z kalkulátora
        }
    
    def load_from_calculator(self):
        """Načíta aktuálnu stratégiu z kalkulátora do optimizera"""
        if not hasattr(self, 'last_calc_result') or not self.last_calc_result:
            messagebox.showwarning("Chyba", "Najprv vypočítajte stratégiu v Kalkulátore")
            return
        
        calc = self.last_calc_result
        
        # Nastav optimizer dáta
        self.opt_data['short_strike'] = calc['shortStrike']
        self.opt_data['short_expiry'] = calc['shortExpiry']
        self.opt_data['short_premium'] = calc['shortPremium']
        self.opt_data['long_strike'] = calc['longStrike']
        self.opt_data['long_expiry'] = calc['longExpiry']
        self.opt_data['long_premium'] = calc['longPremium']
        self.opt_data['underlying_price'] = calc['underlyingPrice']
        self.opt_data['option_type'] = calc['optionType']
        self.opt_data['original'] = calc.copy()
        
        # Aktualizuj labels
        self.update_optimizer_labels()
        
        # Aktualizuj entry polia
        self.opt_short_premium_entry.delete(0, tk.END)
        self.opt_short_premium_entry.insert(0, f"{calc['shortPremium']:.2f}")
        self.opt_long_premium_entry.delete(0, tk.END)
        self.opt_long_premium_entry.insert(0, f"{calc['longPremium']:.2f}")
        
        # Aktualizuj current label
        self.opt_current_label.config(
            text=f"{calc['spreadType']}: Short {calc['shortStrike']} @ ${calc['shortPremium']:.2f} | "
                 f"Long {calc['longStrike']} @ ${calc['longPremium']:.2f} | ROI: {calc['weeklyROI']:.2f}%/týždeň"
        )
        
        # Nájdi indexy expirácií
        if self.available_expiries:
            if calc['shortExpiry'] in self.available_expiries:
                self.opt_data['short_expiry_idx'] = self.available_expiries.index(calc['shortExpiry'])
            if calc['longExpiry'] in self.available_expiries:
                self.opt_data['long_expiry_idx'] = self.available_expiries.index(calc['longExpiry'])
        
        self.recalculate_optimizer()
    
    def update_optimizer_labels(self):
        """Aktualizuje labels v optimizer tabe"""
        self.opt_short_strike_label.config(text=f"${self.opt_data['short_strike']:.0f}")
        self.opt_short_expiry_label.config(text=self.opt_data['short_expiry'] or "--------")
        self.opt_long_strike_label.config(text=f"${self.opt_data['long_strike']:.0f}")
        self.opt_long_expiry_label.config(text=self.opt_data['long_expiry'] or "--------")
    
    def adjust_strike(self, leg, delta):
        """Upraví strike o delta"""
        if leg == 'short':
            self.opt_data['short_strike'] += delta
        else:
            self.opt_data['long_strike'] += delta
        self.update_optimizer_labels()
        # Automaticky stiahni nové premium
        self.fetch_premium(leg)
    
    def adjust_expiry(self, leg, delta):
        """Zmení expiráciu na predchádzajúcu/nasledujúcu"""
        if not self.available_expiries:
            messagebox.showwarning("Chyba", "Najprv načítajte expirácie")
            return
        
        if leg == 'short':
            new_idx = self.opt_data['short_expiry_idx'] + delta
            if 0 <= new_idx < len(self.available_expiries):
                self.opt_data['short_expiry_idx'] = new_idx
                self.opt_data['short_expiry'] = self.available_expiries[new_idx]
        else:
            new_idx = self.opt_data['long_expiry_idx'] + delta
            if 0 <= new_idx < len(self.available_expiries):
                self.opt_data['long_expiry_idx'] = new_idx
                self.opt_data['long_expiry'] = self.available_expiries[new_idx]
        
        self.update_optimizer_labels()
        # Automaticky stiahni nové premium
        self.fetch_premium(leg)
    
    def fetch_premium(self, leg):
        """Stiahne premium pre aktuálny strike/expiry v optimizeri"""
        if leg == 'short':
            strike = self.opt_data['short_strike']
            expiry = self.opt_data['short_expiry']
            entry = self.opt_short_premium_entry
            premium_key = 'short_premium'
        else:
            strike = self.opt_data['long_strike']
            expiry = self.opt_data['long_expiry']
            entry = self.opt_long_premium_entry
            premium_key = 'long_premium'
        
        if not strike or not expiry:
            messagebox.showwarning("Chyba", "Nastavte strike a expiry")
            return
        
        right = 'C' if self.opt_data['option_type'] == 'CALL' else 'P'
        symbol = self.symbol_var.get()
        port = self.port_var.get()
        
        self.update_calc_status(f"Sťahujem {leg} premium...")
        
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_option.py')
                result = subprocess.run(
                    ['python3', script_path, str(port), symbol, expiry, str(strike), right], 
                    capture_output=True, text=True, timeout=20,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                
                output = result.stdout.strip()
                
                if output.startswith("ERROR:"):
                    error_msg = output.replace("ERROR:", "")
                    self.root.after(0, lambda msg=error_msg, lt=leg: self.update_calc_status(f"❌ {lt}: {msg}"))
                elif result.returncode == 0 and output:
                    try:
                        price = float(output)
                        if price > 0:
                            # Aktualizuj entry pole
                            self.root.after(0, lambda e=entry, val=output: self._update_premium_entry(e, val))
                            # Aktualizuj opt_data
                            self.root.after(0, lambda key=premium_key, val=price: self._update_opt_premium(key, val))
                            self.root.after(0, lambda lt=leg, st=strike, val=output: self.update_calc_status(
                                f"✓ {lt.upper()} {st} @ ${val}"))
                            # Automaticky prepočítaj
                            self.root.after(100, self.recalculate_optimizer)
                        else:
                            self.root.after(0, lambda lt=leg: self.update_calc_status(f"❌ {lt}: Cena = 0"))
                    except ValueError:
                        self.root.after(0, lambda out=output: self.update_calc_status(f"❌ Neplatná odpoveď: {out}"))
                elif not output:
                    self.root.after(0, lambda: self.update_calc_status(f"❌ TWS neodpovedá"))
                else:
                    self.root.after(0, lambda: self.update_calc_status(f"❌ Nepodarilo sa načítať premium"))
                        
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self.update_calc_status(f"❌ Timeout"))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self.update_calc_status(f"❌ {err}"))
        
        threading.Thread(target=run, daemon=True).start()
    
    def _update_premium_entry(self, entry, value):
        """Helper na aktualizáciu entry poľa"""
        entry.delete(0, tk.END)
        entry.insert(0, value)
    
    def _update_opt_premium(self, key, value):
        """Helper na aktualizáciu opt_data premium"""
        self.opt_data[key] = value
    
    def recalculate_optimizer(self):
        """Prepočíta stratégiu s aktuálnymi hodnotami"""
        # Získaj premium z entry polí
        try:
            self.opt_data['short_premium'] = float(self.opt_short_premium_entry.get() or 0)
            self.opt_data['long_premium'] = float(self.opt_long_premium_entry.get() or 0)
        except ValueError:
            pass
        
        # Vypočítaj novú stratégiu
        new_calc = self.calculate_spread_internal(
            self.opt_data['short_strike'],
            self.opt_data['short_premium'],
            self.opt_data['short_expiry'],
            self.opt_data['long_strike'],
            self.opt_data['long_premium'],
            self.opt_data['long_expiry'],
            self.opt_data['underlying_price'],
            self.opt_data['option_type']
        )
        
        # Porovnaj s pôvodnou
        orig = self.opt_data.get('original')
        
        compare_text = self.format_comparison(orig, new_calc)
        
        self.opt_compare_text.delete(1.0, tk.END)
        self.opt_compare_text.insert(tk.END, compare_text)
    
    def calculate_spread_internal(self, short_strike, short_premium, short_expiry,
                                   long_strike, long_premium, long_expiry,
                                   underlying_price, option_type):
        """Interný výpočet spreadu - vracia dict"""
        from datetime import datetime
        
        spread_width = abs(short_strike - long_strike) if long_strike > 0 else 0
        same_expiry = (short_expiry == long_expiry) or not long_expiry
        
        # DTE
        today = datetime.now()
        if short_expiry:
            try:
                short_exp_date = datetime.strptime(short_expiry, '%Y%m%d')
                short_dte = max(1, (short_exp_date - today).days)
            except:
                short_dte = 7
        else:
            short_dte = 7
        
        if long_expiry:
            try:
                long_exp_date = datetime.strptime(long_expiry, '%Y%m%d')
                long_dte = max(1, (long_exp_date - today).days)
            except:
                long_dte = short_dte
        else:
            long_dte = short_dte
        
        # Net credit/debit
        net_amount = short_premium - long_premium
        is_credit = net_amount > 0
        
        # Typ spreadu
        if same_expiry:
            if spread_width == 0:
                spread_type = f"Single {option_type}"
            elif is_credit:
                spread_type = f"Vertical CREDIT ({option_type})"
            else:
                spread_type = f"Vertical DEBIT ({option_type})"
        else:
            if spread_width == 0:
                spread_type = f"Calendar Spread ({option_type})"
            elif is_credit:
                spread_type = f"Diagonal CREDIT"
            else:
                spread_type = f"PMCC" if option_type == 'CALL' else "PMCP"
        
        if is_credit:
            net_credit = net_amount
            max_profit = net_credit * 100
            max_loss = (spread_width - net_credit) * 100 if spread_width > 0 else float('inf')
            
            # Margin pre CREDIT spread
            broker = self.broker_var.get()
            broker_pct = 0.10 if broker == 'IBKR' else 0.15
            if spread_width > 0 and same_expiry:
                margin = spread_width * 100
            elif spread_width > 0:
                margin = spread_width * 100 * 1.2
            else:
                margin = underlying_price * broker_pct * 100
            
            if option_type == 'PUT':
                break_even = short_strike - net_credit
            else:
                break_even = short_strike + net_credit
            
            if margin > 0:
                total_roi = (net_credit * 100 / margin) * 100
                weekly_roi = total_roi / short_dte * 7
            else:
                weekly_roi = 0
        else:
            net_debit = abs(net_amount)
            max_loss = net_debit * 100
            
            broker = self.broker_var.get()
            broker_pct = 0.10 if broker == 'IBKR' else 0.15
            
            if same_expiry and spread_width > 0:
                # Vertical debit
                max_profit = (spread_width - net_debit) * 100
                additional_margin = 0
            elif spread_width == 0:
                # CALENDAR SPREAD
                max_profit = float('inf')
                if broker == 'IBKR':
                    additional_margin = long_premium * 100 * 0.15
                else:
                    additional_margin = 0
            else:
                # DIAGONAL SPREAD (PMCC/PMCP)
                max_profit = float('inf')
                if broker == 'IBKR':
                    additional_margin = max(spread_width * 100, underlying_price * 0.05 * 100)
                else:
                    additional_margin = spread_width * 100 * 1.5
            
            investment = net_debit * 100
            total_capital = investment + additional_margin
            margin = total_capital
            
            break_even = short_strike + net_debit
            
            if margin > 0:
                profit_for_roi = short_premium * 100 if not same_expiry else max_profit
                if profit_for_roi != float('inf'):
                    total_roi = (profit_for_roi / margin) * 100
                    weekly_roi = total_roi / short_dte * 7
                else:
                    weekly_roi = 0
            else:
                weekly_roi = 0
        
        return {
            'shortStrike': short_strike,
            'shortPremium': short_premium,
            'shortExpiry': short_expiry,
            'shortDTE': short_dte,
            'longStrike': long_strike,
            'longPremium': long_premium,
            'longExpiry': long_expiry,
            'longDTE': long_dte,
            'spreadWidth': spread_width,
            'spreadType': spread_type,
            'isCredit': is_credit,
            'netCredit': net_amount if is_credit else 0,
            'netDebit': abs(net_amount) if not is_credit else 0,
            'maxProfit': max_profit,
            'maxLoss': max_loss,
            'margin': margin,
            'breakEven': break_even,
            'weeklyROI': weekly_roi,
            'underlyingPrice': underlying_price,
            'optionType': option_type,
        }
    
    def format_comparison(self, orig, new):
        """Formátuje porovnanie pôvodnej a novej stratégie"""
        if not orig:
            # Len nová stratégia
            return self.format_single_strategy(new, "AKTUÁLNA STRATÉGIA")
        
        # Porovnanie
        def delta_str(new_val, orig_val, fmt=".2f", suffix="", invert=False):
            if new_val == float('inf') or orig_val == float('inf'):
                return "∞"
            diff = new_val - orig_val
            if invert:
                diff = -diff
            sign = "+" if diff >= 0 else ""
            return f"{sign}{diff:{fmt}}{suffix}"
        
        # Použijeme additional margin pre porovnanie (rovnako ako kalkulátor)
        orig_margin_display = orig.get('additionalMargin', orig['margin'])
        new_margin_display = new.get('additionalMargin', new['margin'])
        
        result = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    📊 POROVNANIE STRATÉGIÍ                                   ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                      PÔVODNÁ              →        UPRAVENÁ         ZMENA    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Typ:         {orig['spreadType']:20}    {new['spreadType']:20}          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  SHORT:       ${orig['shortStrike']:<7.0f} @ ${orig['shortPremium']:<5.2f}      ${new['shortStrike']:<7.0f} @ ${new['shortPremium']:<5.2f}           ║
║  LONG:        ${orig['longStrike']:<7.0f} @ ${orig['longPremium']:<5.2f}      ${new['longStrike']:<7.0f} @ ${new['longPremium']:<5.2f}           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Net:         ${orig.get('netCredit', 0) or -orig.get('netDebit', 0):<10.2f}         ${new.get('netCredit', 0) or -new.get('netDebit', 0):<10.2f}    {delta_str((new.get('netCredit', 0) or -new.get('netDebit', 0)), (orig.get('netCredit', 0) or -orig.get('netDebit', 0)))}     ║
║  Dod. Margin: ${orig_margin_display:<10.2f}         ${new_margin_display:<10.2f}    {delta_str(new_margin_display, orig_margin_display, ".2f", "", True)}     ║
║  Weekly ROI:  {orig['weeklyROI']:<10.2f}%        {new['weeklyROI']:<10.2f}%   {delta_str(new['weeklyROI'], orig['weeklyROI'])}%    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Break-Even:  ${orig['breakEven']:<10.2f}         ${new['breakEven']:<10.2f}    {delta_str(new['breakEven'], orig['breakEven'])}     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
        
        # Hodnotenie
        roi_diff = new['weeklyROI'] - orig['weeklyROI']
        if roi_diff > 0.5:
            result += f"\n✅ LEPŠIE: ROI zvýšené o {roi_diff:.2f}%"
        elif roi_diff < -0.5:
            result += f"\n⚠️ HORŠIE: ROI znížené o {abs(roi_diff):.2f}%"
        else:
            result += f"\n➡️ PODOBNÉ: ROI rozdiel {roi_diff:.2f}%"
        
        return result
    
    def format_single_strategy(self, calc, title):
        """Formátuje jednu stratégiu"""
        net_str = f"${calc.get('netCredit', 0):.2f}" if calc['isCredit'] else f"-${calc.get('netDebit', 0):.2f}"
        max_profit_str = f"${calc['maxProfit']:.0f}" if calc['maxProfit'] != float('inf') else "∞"
        max_loss_str = f"${calc['maxLoss']:.0f}" if calc['maxLoss'] != float('inf') else "∞"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║  {title:^60}  ║
╠══════════════════════════════════════════════════════════════════╣
║  Typ: {calc['spreadType']:55} ║
║  SHORT: ${calc['shortStrike']:.0f} @ ${calc['shortPremium']:.2f} (DTE: {calc['shortDTE']})                          ║
║  LONG:  ${calc['longStrike']:.0f} @ ${calc['longPremium']:.2f} (DTE: {calc['longDTE']})                          ║
╠══════════════════════════════════════════════════════════════════╣
║  Net:        {net_str:15}    Max Profit: {max_profit_str:12}   ║
║  Margin:     ${calc['margin']:<13.0f}    Max Loss:   {max_loss_str:12}   ║
║  Weekly ROI: {calc['weeklyROI']:<13.2f}%   Break-Even: ${calc['breakEven']:<10.2f}   ║
╚══════════════════════════════════════════════════════════════════╝
"""
    
    def apply_to_calculator(self):
        """Prenesie hodnoty z optimizera späť do kalkulátora"""
        self.calc_short_strike_var.set(str(self.opt_data['short_strike']))
        self.calc_short_expiry_var.set(self.opt_data['short_expiry'])
        self.calc_short_premium_var.set(self.opt_short_premium_entry.get())
        
        self.calc_long_strike_var.set(str(self.opt_data['long_strike']))
        self.calc_long_expiry_var.set(self.opt_data['long_expiry'])
        self.calc_long_premium_var.set(self.opt_long_premium_entry.get())
        
        messagebox.showinfo("Hotovo", "Hodnoty prenesené do Kalkulátora")
    
    def create_roll_optimizer_tab(self, parent):
        """Záložka pre optimalizáciu rolovania LONG legu pri Calendar/Diagonal spreadoch"""
        
        # === Aktuálna pozícia ===
        position_frame = ttk.LabelFrame(parent, text="📋 Aktuálna LONG pozícia", padding=10)
        position_frame.pack(fill='x', padx=10, pady=5)
        
        # Riadok 1: Symbol, Typ, Underlying
        row1 = ttk.Frame(position_frame)
        row1.pack(fill='x', pady=3)
        
        ttk.Label(row1, text="Symbol:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.symbol_var, width=8).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Typ:").pack(side='left', padx=5)
        ttk.Combobox(row1, textvariable=self.option_type_var, values=["PUT", "CALL"], width=6).pack(side='left', padx=5)
        
        ttk.Label(row1, text="Cena podkladu $:").pack(side='left', padx=5)
        ttk.Entry(row1, textvariable=self.roll_underlying_var, width=10).pack(side='left', padx=5)
        ttk.Button(row1, text="📥", width=3, command=self.roll_fetch_underlying).pack(side='left', padx=2)
        
        # Riadok 2: Aktuálny LONG leg
        row2 = ttk.Frame(position_frame)
        row2.pack(fill='x', pady=3)
        
        ttk.Label(row2, text="LONG Strike:").pack(side='left', padx=5)
        ttk.Entry(row2, textvariable=self.roll_current_strike_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row2, text="Expiry:").pack(side='left', padx=5)
        self.roll_expiry_combo = ttk.Combobox(row2, textvariable=self.roll_current_expiry_var, width=12)
        self.roll_expiry_combo.pack(side='left', padx=5)
        
        ttk.Label(row2, text="Aktuálna cena $:").pack(side='left', padx=5)
        ttk.Entry(row2, textvariable=self.roll_current_premium_var, width=8).pack(side='left', padx=5)
        ttk.Button(row2, text="📥", width=3, command=self.roll_fetch_current_premium).pack(side='left', padx=2)
        
        ttk.Label(row2, text="DTE:").pack(side='left', padx=5)
        ttk.Entry(row2, textvariable=self.roll_current_dte_var, width=5, state='readonly').pack(side='left', padx=2)
        
        # Riadok 3: Investícia a prijatý kredit
        row3 = ttk.Frame(position_frame)
        row3.pack(fill='x', pady=3)
        
        ttk.Label(row3, text="Celková investícia (Net Debit) $:").pack(side='left', padx=5)
        ttk.Entry(row3, textvariable=self.roll_total_invested_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row3, text="Už prijatý kredit (short exp) $:").pack(side='left', padx=5)
        ttk.Entry(row3, textvariable=self.roll_received_credit_var, width=10).pack(side='left', padx=5)
        
        ttk.Button(row3, text="🔄 Načítať expirácie", command=self.load_expiries).pack(side='left', padx=10)
        ttk.Button(row3, text="📂 Z Kalkulátora", command=self.roll_load_from_calculator).pack(side='left', padx=5)
        
        # === Tlačidlo analýzy ===
        btn_frame = ttk.Frame(position_frame)
        btn_frame.pack(fill='x', pady=10)
        
        ttk.Button(btn_frame, text="📊 ANALYZOVAŤ ROLL SCENÁRE", 
                   command=self.analyze_roll_scenarios, style='Accent.TButton').pack(side='left', padx=5)
        
        self.roll_status_label = ttk.Label(btn_frame, text="Pripravené")
        self.roll_status_label.pack(side='left', padx=20)
        
        # === Tabuľka roll scenárov ===
        table_frame = ttk.LabelFrame(parent, text="📊 Roll scenáre (zoradené podľa skóre)", padding=10)
        table_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Treeview pre roll scenáre
        columns = ('action', 'new_strike', 'new_expiry', 'cost', 'break_even', 'prob_profit', 'score')
        self.roll_tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=10)
        
        self.roll_tree.heading('action', text='Akcia')
        self.roll_tree.heading('new_strike', text='Nový Strike')
        self.roll_tree.heading('new_expiry', text='Nová Expiry')
        self.roll_tree.heading('cost', text='Doplatok $')
        self.roll_tree.heading('break_even', text='Break-Even')
        self.roll_tree.heading('prob_profit', text='P(Profit)')
        self.roll_tree.heading('score', text='Skóre ★')
        
        self.roll_tree.column('action', width=120, anchor='center')
        self.roll_tree.column('new_strike', width=90, anchor='center')
        self.roll_tree.column('new_expiry', width=100, anchor='center')
        self.roll_tree.column('cost', width=90, anchor='center')
        self.roll_tree.column('break_even', width=90, anchor='center')
        self.roll_tree.column('prob_profit', width=80, anchor='center')
        self.roll_tree.column('score', width=80, anchor='center')
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.roll_tree.yview)
        self.roll_tree.configure(yscrollcommand=scrollbar.set)
        
        self.roll_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Bind pre výber riadku
        self.roll_tree.bind('<<TreeviewSelect>>', self.on_roll_scenario_select)
        
        # === Detaily vybraného scenára ===
        detail_frame = ttk.LabelFrame(parent, text="📝 Detail a odporúčanie", padding=10)
        detail_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        self.roll_detail_text = scrolledtext.ScrolledText(detail_frame, height=12, font=('Courier', 10))
        self.roll_detail_text.pack(fill='both', expand=True)
        
        # Inicializuj roll_scenarios
        self.roll_scenarios = []
    
    def roll_fetch_underlying(self):
        """Stiahne cenu podkladu pre Roll Optimizer"""
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_price.py')
                result = subprocess.run(
                    ['python3', script_path, str(self.port_var.get()), self.symbol_var.get()], 
                    capture_output=True, text=True, timeout=20,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                output = result.stdout.strip().split('\n')[0]
                if result.returncode == 0 and output and not output.startswith("ERROR:"):
                    self.root.after(0, lambda p=output: self.roll_underlying_var.set(p))
                    self.root.after(0, lambda: self.roll_status_label.config(text=f"✓ Cena: ${output}"))
                else:
                    self.root.after(0, lambda: self.roll_status_label.config(text="❌ Chyba načítania ceny"))
            except Exception as e:
                self.root.after(0, lambda: self.roll_status_label.config(text=f"❌ {str(e)[:30]}"))
        
        self.roll_status_label.config(text="Sťahujem cenu...")
        threading.Thread(target=run, daemon=True).start()
    
    def roll_fetch_current_premium(self):
        """Stiahne aktuálnu cenu LONG opcie"""
        strike = self.roll_current_strike_var.get()
        expiry = self.roll_current_expiry_var.get()
        
        if not strike or not expiry:
            messagebox.showwarning("Chyba", "Zadajte strike a expiry")
            return
        
        right = 'C' if self.option_type_var.get() == 'CALL' else 'P'
        symbol = self.symbol_var.get()
        port = self.port_var.get()
        
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_option.py')
                result = subprocess.run(
                    ['python3', script_path, str(port), symbol, expiry, str(strike), right], 
                    capture_output=True, text=True, timeout=20,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                output = result.stdout.strip()
                if result.returncode == 0 and output and not output.startswith("ERROR:"):
                    price = float(output)
                    self.root.after(0, lambda: self.roll_current_premium_var.set(f"{price:.2f}"))
                    self.root.after(0, lambda: self.roll_status_label.config(text=f"✓ LONG @ ${price:.2f}"))
                    # Vypočítaj DTE
                    self.root.after(0, self.roll_update_dte)
                else:
                    self.root.after(0, lambda: self.roll_status_label.config(text="❌ Chyba načítania premium"))
            except Exception as e:
                self.root.after(0, lambda: self.roll_status_label.config(text=f"❌ {str(e)[:30]}"))
        
        self.roll_status_label.config(text="Sťahujem premium...")
        threading.Thread(target=run, daemon=True).start()
    
    def roll_update_dte(self):
        """Aktualizuje DTE pre aktuálnu LONG pozíciu"""
        expiry = self.roll_current_expiry_var.get()
        if expiry:
            try:
                from datetime import datetime
                exp_date = datetime.strptime(expiry, '%Y%m%d')
                dte = max(0, (exp_date - datetime.now()).days)
                self.roll_current_dte_var.set(str(dte))
            except:
                self.roll_current_dte_var.set("?")
    
    def roll_load_from_calculator(self):
        """Načíta LONG pozíciu z kalkulátora"""
        if not hasattr(self, 'last_calc_result') or not self.last_calc_result:
            messagebox.showwarning("Chyba", "Najprv vypočítajte stratégiu v Kalkulátore")
            return
        
        calc = self.last_calc_result
        
        # Nastav hodnoty
        self.roll_current_strike_var.set(str(calc['longStrike']))
        self.roll_current_expiry_var.set(calc['longExpiry'] or '')
        self.roll_current_premium_var.set(f"{calc['longPremium']:.2f}")
        self.roll_underlying_var.set(f"{calc['underlyingPrice']:.2f}")
        
        # Net debit ako celková investícia
        if not calc['isCredit']:
            net_debit = calc.get('netDebit', 0) or abs(calc['longPremium'] - calc['shortPremium'])
            self.roll_total_invested_var.set(f"{net_debit * 100:.2f}")
        
        # Ak short už expiroval, jeho premium je prijatý kredit
        self.roll_received_credit_var.set(f"{calc['shortPremium'] * 100:.2f}")
        
        # Aktualizuj DTE
        self.roll_update_dte()
        
        self.roll_status_label.config(text="✓ Načítané z Kalkulátora")
    
    def create_balancer_tab(self, parent):
        """Balancer: zadajte LONG leg a nájde opačnú opciu (same expiry), tlačidlo na stiahnutie presnej ceny"""
        frame = ttk.LabelFrame(parent, text="Balancer - vybalansovanie delty", padding=10)
        frame.pack(fill='x', padx=10, pady=10)
        
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=5)
        
        ttk.Label(row, text="LongTyp 2:").pack(side='left', padx=5)
        ttk.Combobox(row, textvariable=self.bal_long_type_var, values=["CALL", "PUT"], width=6).pack(side='left', padx=5)
        ttk.Button(row, text="📥 Z Kalkulátora", command=self.bal_load_from_calculator).pack(side='left', padx=6)
        ttk.Label(row, textvariable=self.bal_type_note_var, foreground='blue').pack(side='left', padx=8)
        
        ttk.Label(row, text="LONG Strike:").pack(side='left', padx=5)
        ttk.Entry(row, textvariable=self.bal_long_strike_var, width=10).pack(side='left', padx=5)
        
        ttk.Label(row, text="Expiry (YYYYMMDD):").pack(side='left', padx=5)
        ttk.Entry(row, textvariable=self.bal_long_expiry_var, width=12).pack(side='left', padx=5)
        
        ttk.Label(row, text="Premium $:").pack(side='left', padx=5)
        ttk.Entry(row, textvariable=self.bal_long_premium_var, width=8).pack(side='left', padx=5)
        
        ttk.Label(row, text="Underlying $:").pack(side='left', padx=5)
        ttk.Entry(row, textvariable=self.bal_underlying_var, width=8).pack(side='left', padx=5)
        ttk.Button(row, text="📥 Stiahnuť podklad", command=self.fetch_bal_underlying).pack(side='left', padx=6)
        
        ttk.Label(row, text="IV:").pack(side='left', padx=5)
        ttk.Entry(row, textvariable=self.bal_iv_var, width=6).pack(side='left', padx=5)
        
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill='x', pady=8)
        
        ttk.Button(btn_row, text="🔍 ANALYZOVAŤ", command=self.analyze_balancer).pack(side='left', padx=8)
        ttk.Button(btn_row, text="✅ APLIKOVAŤ (simulácia)", command=self.apply_balance).pack(side='left', padx=5)
        
        # Plot controls
        plot_row = ttk.Frame(frame)
        plot_row.pack(fill='x', pady=6)
        ttk.Label(plot_row, text="Zobraziť:").pack(side='left', padx=5)
        ttk.Combobox(plot_row, textvariable=self.bal_plot_metric_var, values=['Price', 'Delta'], width=8).pack(side='left', padx=5)
        ttk.Label(plot_row, text="Rozsah (×ATR):").pack(side='left', padx=5)
        ttk.Entry(plot_row, textvariable=self.bal_plot_atr_mult_var, width=6).pack(side='left', padx=5)
        ttk.Button(plot_row, text="📈 Zobraziť graf", command=self.show_balancer_plot).pack(side='left', padx=8)
        
        result_frame = ttk.LabelFrame(parent, text="Výsledok", padding=10)
        result_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.bal_results_text = scrolledtext.ScrolledText(result_frame, height=12, font=('Courier', 10))
        self.bal_results_text.pack(fill='both', expand=True)
        
        bottom_row = ttk.Frame(parent)
        bottom_row.pack(fill='x', padx=10, pady=6)
        ttk.Label(bottom_row, text="Opp. Strike:").pack(side='left', padx=5)
        ttk.Entry(bottom_row, textvariable=self.bal_opposite_strike_var, width=10).pack(side='left', padx=5)
        ttk.Button(bottom_row, text="→ .50", width=6, command=self.set_opposite_strike_half).pack(side='left', padx=3)
        ttk.Button(bottom_row, text="→ .00", width=6, command=self.set_opposite_strike_integer).pack(side='left', padx=3)
        ttk.Label(bottom_row, text="Opp. Premium:").pack(side='left', padx=5)
        ttk.Entry(bottom_row, textvariable=self.bal_opposite_premium_var, width=10, state='readonly').pack(side='left', padx=5)
        ttk.Button(bottom_row, text="📥 Stiahnuť presnú cenu", command=self.fetch_balancer_option_price).pack(side='left', padx=8)
        
        self.bal_status_label = ttk.Label(parent, text="Pripravené")
        self.bal_status_label.pack(fill='x', padx=10, pady=4)

    def bal_load_from_calculator(self):
        """Načíta LONG leg z Kalkulátora a nastaví LongTyp 2 opačne podľa kalkulátora"""
        if not hasattr(self, 'last_calc_result') or not self.last_calc_result:
            messagebox.showwarning("Chyba", "Najprv vypočítajte stratégiu v Kalkulátore")
            return
        calc = self.last_calc_result
        try:
            self.bal_long_strike_var.set(str(calc.get('longStrike')))
            self.bal_long_expiry_var.set(calc.get('longExpiry') or '')
            self.bal_long_premium_var.set(f"{calc.get('longPremium'):.2f}")
            self.bal_underlying_var.set(f"{calc.get('underlyingPrice'):.2f}")
        except Exception:
            # fallback na polia z kalkulátora
            self.bal_long_strike_var.set(self.calc_long_strike_var.get())
            self.bal_long_expiry_var.set(self.calc_long_expiry_var.get())
            self.bal_long_premium_var.set(self.calc_long_premium_var.get())
            self.bal_underlying_var.set(self.calc_underlying_price_var.get() or '')
        # Set long type from calculator (keep LONG same as in calculator)
        calc_type = self.option_type_var.get()
        self.bal_long_type_var.set(calc_type)
        # Set opposite type in opposite field for clarity
        opp_type = 'CALL' if calc_type == 'PUT' else 'PUT'
        self.bal_opposite_type_var.set(opp_type)
        self.bal_type_note_var.set(f"Opposite suggestion: {opp_type}")
        # sync IV
        self.bal_iv_var.set(self.iv_var.get())
        self.bal_status_label.config(text="✓ Načítané z Kalkulátora")
        messagebox.showinfo("Balancer", "LONG bol načítaný z Kalkulátora (LongTyp 2 nastavený podľa Kalkulátora)")

    def fetch_bal_underlying(self):
        """Stiahne cenu podkladu rovnakým spôsobom ako v kalkulátore"""
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_price.py')
                result = subprocess.run(['python3', script_path, str(self.port_var.get()), self.symbol_var.get()],
                                        capture_output=True, text=True, timeout=20,
                                        cwd='/home/narbon/Aplikácie/tws-webapp')
                out = result.stdout.strip().split('\n')[0] if result.stdout else ''
                if result.returncode == 0 and out and not out.startswith("ERROR:"):
                    self.root.after(0, lambda v=out: self.bal_underlying_var.set(v))
                    self.root.after(0, lambda: self.bal_status_label.config(text=f"✓ Podklad: ${out}"))
                else:
                    self.root.after(0, lambda: self.bal_status_label.config(text="❌ Chyba pri sťahovaní podkladu"))
            except Exception as e:
                self.root.after(0, lambda: self.bal_status_label.config(text=f"❌ {str(e)[:30]}"))
        self.bal_status_label.config(text="Sťahujem cenu podkladu...")
        threading.Thread(target=run, daemon=True).start()

    def get_time_to_expiry_years(self, expiry):
        """Return time to expiry in years (float)."""
        try:
            exp_date = datetime.strptime(expiry, '%Y%m%d').date()
            days = max(0, (exp_date - date.today()).days)
            return max(1/365.0, days / 365.0)
        except Exception:
            return 7/365.0

    def show_balancer_plot(self):
        """Plot P/L between LONG and Opposite option across underlying ± ATR.
        Y axis: P/L in $ per pair (LONG - OPP) * 100 (per share multiplier).
        """
        if not MATPLOTLIB_AVAILABLE:
            messagebox.showerror("Chyba", "Na zobrazenie grafu je potrebný matplotlib (pip install matplotlib)")
            return
        try:
            atr_mult = float(self.bal_plot_atr_mult_var.get() or 1.0)
            S0 = float(self.bal_underlying_var.get() or self.calc_underlying_price_var.get() or 0)
            if not S0 or S0 <= 0:
                messagebox.showwarning("Chyba", "Zadajte cenu podkladu")
                return

            # ATR fallback
            atr = self.atr_7d if self.atr_7d and self.atr_7d > 0 else max(0.01, S0 * 0.02)
            low = max(0.01, S0 - atr * atr_mult)
            high = S0 + atr * atr_mult

            # Long params
            long_type = self.bal_long_type_var.get()
            long_strike = float(self.bal_long_strike_var.get() or 0)
            expiry = self.bal_long_expiry_var.get()
            iv = float(self.bal_iv_var.get() or 0.2)
            r = float(self.rate_var.get() or 0.0)
            T = self.get_time_to_expiry_years(expiry)

            # Opposite params
            opp_type = self.bal_opposite_type_var.get()
            opp_strike = float(self.bal_opposite_strike_var.get() or 0)

            # Build S grid
            n = 121
            Ss = [low + (high - low) * i / (n-1) for i in range(n)]

            pl_vals = []
            long_vals = []
            opp_vals = []
            for S in Ss:
                if long_type == 'CALL':
                    long_price = self.black_scholes_call_price(S, long_strike, T, r, iv)
                else:
                    long_price = self.black_scholes_put_price(S, long_strike, T, r, iv)

                if opp_type == 'CALL':
                    opp_price = self.black_scholes_call_price(S, opp_strike, T, r, iv)
                else:
                    opp_price = self.black_scholes_put_price(S, opp_strike, T, r, iv)

                # P/L per pair = (LONG price - OPP price) * 100
                pl = (long_price - opp_price) * 100.0
                pl_vals.append(pl)
                long_vals.append(long_price)
                opp_vals.append(opp_price)

            # Create plot window
            win = tk.Toplevel(self.root)
            win.title("Balancer - P/L between LONG and OPP vs Underlying")
            fig = plt.Figure(figsize=(7,4.5), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(Ss, pl_vals, label=f'P/L (LONG - OPP) x100', color='tab:blue')
            ax.axhline(0, color='black', linewidth=0.8, linestyle='--')
            ax.axvline(S0, color='gray', linestyle='--', label='Current S')
            ax.axvline(S0 - atr*atr_mult, color='red', linestyle=':', label=f'-{atr_mult}×ATR')
            ax.axvline(S0 + atr*atr_mult, color='red', linestyle=':', label=f'+{atr_mult}×ATR')

            # Annotate current P/L values at S0
            try:
                idx = min(range(len(Ss)), key=lambda i: abs(Ss[i]-S0))
                cur_pl = pl_vals[idx]
                ax.annotate(f'S0 ${S0:.2f}\nP/L ${cur_pl:.2f}', xy=(S0, cur_pl), xytext=(10, 10), textcoords='offset points', bbox=dict(boxstyle='round', fc='w'))
            except Exception:
                pass

            ax.set_xlabel('Underlying Price')
            ax.set_ylabel('P/L $ per pair')
            ax.grid(True)
            ax.legend()

            canvas = FigureCanvasTkAgg(fig, master=win)
            canvas.draw()
            canvas.get_tk_widget().pack(fill='both', expand=True)

        except Exception as e:
            messagebox.showerror("Chyba", f"Chyba pri kreslení grafu: {e}")
            ax.axvline(S0, color='gray', linestyle='--', label='Current S')
            ax.axvline(S0 - atr*atr_mult, color='red', linestyle=':', label=f'-{atr_mult}×ATR')
            ax.axvline(S0 + atr*atr_mult, color='red', linestyle=':', label=f'+{atr_mult}×ATR')
            ax.set_xlabel('Underlying Price')
            ax.set_ylabel(metric)
            ax.grid(True)
            ax.legend()

            canvas = FigureCanvasTkAgg(fig, master=win)
            canvas.draw()
            canvas.get_tk_widget().pack(fill='both', expand=True)

        except Exception as e:
            messagebox.showerror("Chyba", f"Chyba pri kreslení grafu: {e}")

    def find_strike_for_delta(self, option_type, target_delta, expiry, iv, r, underlying):
        """Hľadá strike tak, aby delta(option, K) == target_delta (brentq ak dostupné, inak grid)"""
        try:
            exp_date = datetime.strptime(expiry, '%Y%m%d').date()
            T = max(1, (exp_date - date.today()).days) / 365.0
        except Exception:
            T = 7/365.0
        
        def delta_for_K(K):
            if option_type == 'CALL':
                return self.black_scholes_delta_call(underlying, K, T, r, iv)
            else:
                return self.black_scholes_delta_put(underlying, K, T, r, iv)
        
        low = max(0.01, underlying * 0.2)
        high = underlying * 3.0
        
        # Try bracketing + brentq if scipy available
        if SCIPY_AVAILABLE:
            steps = 60
            ks = [low + (high - low) * i / steps for i in range(steps + 1)]
            fvals = [delta_for_K(k) - target_delta for k in ks]
            for i in range(len(ks) - 1):
                if fvals[i] == 0:
                    return round(ks[i], 2)
                if fvals[i] * fvals[i + 1] < 0:
                    try:
                        root = brentq(lambda K: delta_for_K(K) - target_delta, ks[i], ks[i+1])
                        return round(root, 2)
                    except Exception:
                        break
        
        # Fallback: nearest in grid
        best_k = None
        best_diff = float('inf')
        for k in [low + (high-low)*i/200 for i in range(201)]:
            diff = abs(delta_for_K(k) - target_delta)
            if diff < best_diff:
                best_diff = diff
                best_k = k
        return round(best_k, 2) if best_k else None

    def analyze_balancer(self):
        """Analyzuje LONG a nájde opačnú opciu tak, aby súčet delt ≈ 0 (ponúkne strike)"""
        try:
            if not SCIPY_AVAILABLE:
                messagebox.showerror("Chyba", "Na analýzu je potrebné scipy (norm/brentq). Nainštalujte scipy.")
                return
            long_type = self.bal_long_type_var.get()
            long_str = self.bal_long_strike_var.get()
            expiry = self.bal_long_expiry_var.get()
            underlying = float(self.bal_underlying_var.get() or 0)
            iv = float(self.bal_iv_var.get() or 0.20)
            r = float(self.rate_var.get() or 0.0)
            
            if not long_str or not expiry or underlying <= 0:
                messagebox.showwarning("Chyba", "Zadajte LONG strike, expiry a cenu podkladu")
                return
            
            long_strike = float(long_str)
            # DTE
            try:
                exp_date = datetime.strptime(expiry, '%Y%m%d').date()
                T = max(1, (exp_date - date.today()).days) / 365.0
            except:
                T = 7/365.0
            
            # delta LONG
            if long_type == 'CALL':
                long_delta = self.black_scholes_delta_call(underlying, long_strike, T, r, iv)
                opp_type = 'PUT'
            else:
                long_delta = self.black_scholes_delta_put(underlying, long_strike, T, r, iv)
                opp_type = 'CALL'
            
            target_delta = -long_delta
            opp_strike = self.find_strike_for_delta(opp_type, target_delta, expiry, iv, r, underlying)
            
            if not opp_strike:
                self.bal_results_text.delete(1.0, tk.END)
                self.bal_results_text.insert(tk.END, "Nepodarilo sa nájsť vhodný strike pre opačnú opciu. Skontrolujte vstupy.")
                self.bal_status_label.config(text="Analýza: neúspech")
                return
            
            # odhad ceny opačnej opcie (Black-Scholes)
            if opp_type == 'CALL':
                opp_price = self.black_scholes_call_price(underlying, opp_strike, T, r, iv)
                opp_delta = self.black_scholes_delta_call(underlying, opp_strike, T, r, iv)
            else:
                opp_price = self.black_scholes_put_price(underlying, opp_strike, T, r, iv)
                opp_delta = self.black_scholes_delta_put(underlying, opp_strike, T, r, iv)
            
            text = (
                f"LONG: {long_type} strike {long_strike:.2f} | delta {long_delta:.4f}\n"
                f"OPPOSITE (sugest): {opp_type} strike {opp_strike:.2f} | est. premium ${opp_price:.2f} | delta {opp_delta:.4f}\n"
                f"SUM DELT: {long_delta + opp_delta:+.4f}\n"
                f"Expiry: {expiry} | Underlying: ${underlying:.2f} | IV: {iv:.2%}\n\n"
                "Tip: stlačte 'Stiahnuť presnú cenu' pre aktualizáciu premium cez API."
            )
            self.bal_results_text.delete(1.0, tk.END)
            self.bal_results_text.insert(tk.END, text)
            self.bal_status_label.config(text="Analýza hotová")
            
            # uloženie návrhu
            self.bal_opposite_strike_var.set(f"{opp_strike:.2f}")
            self.bal_opposite_premium_var.set("")
            self.bal_opposite_type_var.set(opp_type)
            self.bal_last_analysis = {
                'long': {'type': long_type, 'strike': long_strike, 'delta': long_delta, 'expiry': expiry},
                'opposite': {'type': opp_type, 'strike': opp_strike, 'est_price': opp_price, 'delta': opp_delta, 'expiry': expiry}
            }
        except Exception as e:
            messagebox.showerror("Chyba", f"Chyba analýzy: {e}")

    def fetch_balancer_option_price(self):
        """Stiahne presnú cenu pre odporúčanú opačnú opciu cez skript (API)"""
        if not self.bal_last_analysis:
            messagebox.showwarning("Chyba", "Najprv spustite analýzu")
            return
        opp = self.bal_last_analysis['opposite']
        # Allow user override of suggested strike (editable field)
        strike = opp.get('strike')
        try:
            user_val = self.bal_opposite_strike_var.get()
            if user_val:
                strike = float(user_val)
        except Exception:
            pass
        expiry = opp['expiry']
        opt_type = opp['type']
        right = 'C' if opt_type == 'CALL' else 'P'
        symbol = self.symbol_var.get()
        port = self.port_var.get()
        
        self.bal_status_label.config(text=f"Sťahujem cenu {right} strike {strike}...")
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_fetch_option.py')
                result = subprocess.run(
                    ['python3', script_path, str(port), symbol, expiry, str(strike), right],
                    capture_output=True, text=True, timeout=20,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                output = result.stdout.strip()
                if result.returncode == 0 and output and not output.startswith("ERROR:"):
                    price = float(output)
                    self.root.after(0, lambda: self.bal_opposite_premium_var.set(f"{price:.2f}"))
                    self.root.after(0, lambda: self.bal_status_label.config(text=f"✓ Cena: ${price:.2f}"))
                    self.bal_last_analysis['opposite']['price'] = price
                else:
                    err = output or result.stderr.strip()
                    self.root.after(0, lambda: self.bal_status_label.config(text="❌ Chyba pri stiahnutí"))
                    self.root.after(0, lambda: messagebox.showwarning("Chyba", f"Nepodarilo sa stiahnuť cenu:\n{err}"))
            except Exception as e:
                self.root.after(0, lambda: self.bal_status_label.config(text=f"❌ {str(e)[:40]}"))
        threading.Thread(target=run, daemon=True).start()

    def on_bal_opposite_strike_changed(self):
        """Update stored analysis when user edits Opposite strike field"""
        val = self.bal_opposite_strike_var.get()
        try:
            f = float(val)
            if self.bal_last_analysis and 'opposite' in self.bal_last_analysis:
                self.bal_last_analysis['opposite']['strike'] = f
        except Exception:
            pass

    def set_opposite_strike_half(self):
        """Set opposite strike to floor(value) + 0.5 (e.g., 707.82 -> 707.50)"""
        try:
            val = float(self.bal_opposite_strike_var.get())
        except Exception:
            if self.bal_last_analysis and 'opposite' in self.bal_last_analysis:
                val = float(self.bal_last_analysis['opposite'].get('strike', 0))
            else:
                return
        new = math.floor(val) + 0.5
        self.bal_opposite_strike_var.set(f"{new:.2f}")
        self.bal_status_label.config(text=f"Opp. Strike set: {new:.2f}")

    def set_opposite_strike_integer(self):
        """Set opposite strike to ceil(value) (e.g., 707.82 -> 708.00)"""
        try:
            val = float(self.bal_opposite_strike_var.get())
        except Exception:
            if self.bal_last_analysis and 'opposite' in self.bal_last_analysis:
                val = float(self.bal_last_analysis['opposite'].get('strike', 0))
            else:
                return
        new = math.ceil(val)
        self.bal_opposite_strike_var.set(f"{new:.2f}")
        self.bal_status_label.config(text=f"Opp. Strike set: {new:.2f}")

    def apply_balance(self):
        """Simulovaná aplikácia Balanceru (nevykonáva reálne obchody)"""
        if not self.bal_last_analysis:
            messagebox.showwarning("Chyba", "Najprv vykonajte analýzu")
            return
        confirm = messagebox.askyesno("Potvrdenie", "Chcete simulovať aplikáciu Balanceru? (Nebude vykonané v reálnom účte)")
        if not confirm:
            return
        self.bal_status_label.config(text="Aplikujem (simulácia)...")
        self.root.after(400, lambda: (self.bal_status_label.config(text="Hotovo (simulácia)"),
                                      messagebox.showinfo("Simulácia", "Balancer aplikovaný (simulácia)")))

    def analyze_roll_scenarios(self):
        """Analyzuje možné roll scenáre pre LONG pozíciu"""
        try:
            # Získaj vstupné hodnoty
            underlying = float(self.roll_underlying_var.get() or 0)
            current_strike = float(self.roll_current_strike_var.get() or 0)
            current_premium = float(self.roll_current_premium_var.get() or 0)
            current_expiry = self.roll_current_expiry_var.get()
            total_invested = float(self.roll_total_invested_var.get() or 0)
            received_credit = float(self.roll_received_credit_var.get() or 0)
            option_type = self.option_type_var.get()
            
            if not all([underlying, current_strike, current_premium]):
                messagebox.showwarning("Chyba", "Vyplňte všetky povinné polia")
                return
            
            # Vypočítaj zostávajúcu stratu/zisk
            # Net position = received_credit - total_invested + current_premium * 100
            net_position = received_credit - total_invested + (current_premium * 100)
            break_even_needed = total_invested - received_credit
            
            # Generuj roll scenáre
            scenarios = []
            
            # 1. DRŽAŤ - nerolovať
            scenarios.append({
                'action': '🔵 DRŽAŤ',
                'new_strike': current_strike,
                'new_expiry': current_expiry,
                'cost': 0,
                'total_cost': total_invested - received_credit,
                'break_even': self._calc_be_for_roll(option_type, current_strike, break_even_needed / 100, underlying),
                'prob_profit': self._estimate_probability(option_type, underlying, current_strike, int(self.roll_current_dte_var.get() or 14)),
                'note': f'Aktuálna hodnota LONG: ${current_premium * 100:.2f}'
            })
            
            # 2. PREDAŤ - uzavrieť pozíciu
            realized_pnl = received_credit + (current_premium * 100) - total_invested
            scenarios.append({
                'action': '🔴 PREDAŤ',
                'new_strike': '-',
                'new_expiry': '-',
                'cost': -current_premium * 100,
                'total_cost': 0,
                'break_even': '-',
                'prob_profit': 100 if realized_pnl >= 0 else 0,
                'note': f'Realizovaný P/L: ${realized_pnl:.2f}'
            })
            
            # 3. ROLL NA VYŠŠÍ STRIKE (pre PUT = bližšie k ATM)
            if option_type == 'PUT':
                # Pre PUT: vyšší strike = bližšie k cene = drahší
                new_strikes = [
                    current_strike + 3,
                    current_strike + 5,
                    current_strike + 7,
                    current_strike + 10,
                ]
            else:
                # Pre CALL: nižší strike = bližšie k cene = drahší
                new_strikes = [
                    current_strike - 3,
                    current_strike - 5,
                    current_strike - 7,
                    current_strike - 10,
                ]
            
            for new_strike in new_strikes:
                if new_strike <= 0:
                    continue
                    
                # Odhadni cenu novej opcie (jednoduchý model)
                # Delta strike od ATM
                current_distance = abs(underlying - current_strike)
                new_distance = abs(underlying - new_strike)
                
                # Čím bližšie k ATM, tým drahšie
                # Použijeme aproximáciu: cena rastie exponenciálne s blízkosťou k ATM
                if new_distance < current_distance:
                    # Bližšie k ATM - drahšie
                    price_ratio = 1 + (current_distance - new_distance) / underlying * 10
                    estimated_new_premium = current_premium * price_ratio
                else:
                    # Ďalej od ATM - lacnejšie
                    price_ratio = 1 - (new_distance - current_distance) / underlying * 5
                    estimated_new_premium = max(0.10, current_premium * price_ratio)
                
                # Doplatok = nová opcia - predaj starej
                roll_cost = (estimated_new_premium - current_premium) * 100
                new_total_cost = total_invested - received_credit + roll_cost
                
                # Nový break-even
                new_be = self._calc_be_for_roll(option_type, new_strike, new_total_cost / 100, underlying)
                
                # Pravdepodobnosť profitu
                dte = int(self.roll_current_dte_var.get() or 14)
                prob = self._estimate_probability(option_type, underlying, new_strike, dte)
                
                # Skóre = prob * (potenciálny zisk / riziko)
                potential_gain = max(0, (new_strike - new_be) * 100) if option_type == 'PUT' else max(0, (new_be - new_strike) * 100)
                
                scenarios.append({
                    'action': f'🔄 ROLL →${new_strike:.0f}',
                    'new_strike': new_strike,
                    'new_expiry': current_expiry,
                    'cost': roll_cost,
                    'total_cost': new_total_cost,
                    'break_even': new_be,
                    'prob_profit': prob,
                    'note': f'Odhadovaná cena novej opcie: ${estimated_new_premium:.2f}'
                })
            
            # 4. ROLL NA DLHŠIU EXPIRÁCIU (ak sú dostupné)
            if self.available_expiries:
                current_idx = -1
                if current_expiry in self.available_expiries:
                    current_idx = self.available_expiries.index(current_expiry)
                
                for i in range(current_idx + 1, min(current_idx + 4, len(self.available_expiries))):
                    new_expiry = self.available_expiries[i]
                    
                    # Dlhšia expirácia = vyššia časová hodnota
                    try:
                        from datetime import datetime
                        new_exp_date = datetime.strptime(new_expiry, '%Y%m%d')
                        current_exp_date = datetime.strptime(current_expiry, '%Y%m%d')
                        extra_days = (new_exp_date - current_exp_date).days
                        
                        # Theta aproximácia: +0.5-1% hodnoty za deň pri ATM
                        theta_factor = 1 + (extra_days * 0.007)  # ~0.7% za deň
                        estimated_new_premium = current_premium * theta_factor
                        
                        roll_cost = (estimated_new_premium - current_premium) * 100
                        new_total_cost = total_invested - received_credit + roll_cost
                        
                        new_dte = max(1, (new_exp_date - datetime.now()).days)
                        prob = self._estimate_probability(option_type, underlying, current_strike, new_dte)
                        
                        scenarios.append({
                            'action': f'📅 ROLL →{new_expiry}',
                            'new_strike': current_strike,
                            'new_expiry': new_expiry,
                            'cost': roll_cost,
                            'total_cost': new_total_cost,
                            'break_even': self._calc_be_for_roll(option_type, current_strike, new_total_cost / 100, underlying),
                            'prob_profit': prob,
                            'note': f'+{extra_days} dní, odhad: ${estimated_new_premium:.2f}'
                        })
                    except:
                        continue
            
            # Vypočítaj skóre pre každý scenár
            for s in scenarios:
                if s['prob_profit'] > 0 and s['break_even'] != '-':
                    # Skóre = pravdepodobnosť * (1 / relatívne riziko)
                    risk = abs(s['total_cost']) if s['total_cost'] != 0 else 1
                    s['score'] = s['prob_profit'] * 100 / max(risk, 1)
                else:
                    s['score'] = s['prob_profit']
            
            # Zoraď podľa skóre
            scenarios.sort(key=lambda x: x['score'], reverse=True)
            
            # Ulož a zobraz
            self.roll_scenarios = scenarios
            self._display_roll_scenarios()
            
            # Zobraz odporúčanie
            self._show_roll_recommendation(scenarios, net_position, underlying, option_type)
            
        except Exception as e:
            messagebox.showerror("Chyba", f"Chyba analýzy: {e}")
            import traceback
            traceback.print_exc()
    
    def _calc_be_for_roll(self, option_type, strike, cost_per_share, underlying):
        """Vypočíta break-even pre roll pozíciu"""
        if option_type == 'PUT':
            # PUT: potrebujeme pokles pod (strike - cost)
            return strike - cost_per_share
        else:
            # CALL: potrebujeme rast nad (strike + cost)
            return strike + cost_per_share
    
    def _estimate_probability(self, option_type, underlying, strike, dte):
        """Odhadne pravdepodobnosť profitu pomocou jednoduchého modelu"""
        # Použijeme normálne rozdelenie s volatilitou ~20% ročne
        # Pre presnejšie výsledky by sme potrebovali skutočnú IV
        
        annual_vol = 0.20  # 20% ročná volatilita
        daily_vol = annual_vol / math.sqrt(252)
        period_vol = daily_vol * math.sqrt(dte)
        
        # Percentuálna vzdialenosť od strike
        distance_pct = (underlying - strike) / underlying
        
        # Z-score
        if period_vol > 0:
            z_score = distance_pct / period_vol
        else:
            z_score = 0
        
        if SCIPY_AVAILABLE:
            if option_type == 'PUT':
                # PUT profituje ak cena klesne pod strike
                prob = norm.cdf(-z_score) * 100
            else:
                # CALL profituje ak cena stúpne nad strike
                prob = (1 - norm.cdf(-z_score)) * 100
        else:
            # Jednoduchá aproximácia bez scipy
            prob = max(5, min(95, 50 - z_score * 20))
        
        return round(prob, 1)
    
    def _display_roll_scenarios(self):
        """Zobrazí roll scenáre v tabuľke"""
        # Vymaž existujúce riadky
        for item in self.roll_tree.get_children():
            self.roll_tree.delete(item)
        
        # Pridaj nové riadky
        for s in self.roll_scenarios:
            be_str = f"${s['break_even']:.2f}" if isinstance(s['break_even'], (int, float)) else s['break_even']
            cost_str = f"${s['cost']:.2f}" if s['cost'] >= 0 else f"-${abs(s['cost']):.2f}"
            
            # Zvýrazni najlepší scenár
            tags = ()
            if s == self.roll_scenarios[0]:
                tags = ('best',)
            
            self.roll_tree.insert('', 'end', values=(
                s['action'],
                f"${s['new_strike']:.0f}" if isinstance(s['new_strike'], (int, float)) else s['new_strike'],
                s['new_expiry'],
                cost_str,
                be_str,
                f"{s['prob_profit']:.1f}%",
                f"{s['score']:.1f} {'★' if s == self.roll_scenarios[0] else ''}"
            ), tags=tags)
        
        # Štýl pre najlepší riadok
        self.roll_tree.tag_configure('best', background='#90EE90')
    
    def _show_roll_recommendation(self, scenarios, net_position, underlying, option_type):
        """Zobrazí odporúčanie v detail paneli"""
        if not scenarios:
            return
        
        best = scenarios[0]
        
        recommendation = f"""
╔══════════════════════════════════════════════════════════════════╗
║                    📊 ROLL ANALÝZA                               ║
╠══════════════════════════════════════════════════════════════════╣
║  Aktuálna cena podkladu: ${underlying:,.2f}                       ║
║  Aktuálna hodnota pozície: ${net_position:,.2f}                   ║
║  Trend potrebný pre profit: {'DOLE ↓' if option_type == 'PUT' else 'HORE ↑'}             ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  ⭐ NAJLEPŠÍ SCENÁR: {best['action']:40} ║
║                                                                  ║
║     Doplatok:         ${best['cost']:>10,.2f}                            ║
║     Break-Even:       {'${:.2f}'.format(best['break_even']) if isinstance(best['break_even'], (int, float)) else best['break_even']:>10}                            ║
║     P(Profit):        {best['prob_profit']:>10.1f}%                           ║
║     Skóre:            {best['score']:>10.1f}                              ║
║                                                                  ║
║  📝 Poznámka: {best['note'][:45]:45} ║
╚══════════════════════════════════════════════════════════════════╝

📋 ROZHODOVACÍ STROM:

"""
        # Rozhodovací strom
        if best['action'].startswith('🔵'):
            recommendation += """
Ak (cena podkladu sa hýbe správnym smerom) A (DTE > 7):
    → DRŽTE pozíciu
    
Ak (DTE < 7) A (pozícia je v strate):
    → Zvážte PREDAJ alebo ROLL na dlhšiu expiráciu
"""
        elif best['action'].startswith('🔴'):
            recommendation += """
Pozícia má malú šancu na profit.

Odporúčam:
    → PREDAŤ a realizovať zostatok
    → Prípadne počkať na lepší vstup do novej pozície
"""
        elif best['action'].startswith('🔄'):
            recommendation += f"""
Roll na vyšší strike je výhodný ak:
    → Očakávate {'pokles' if option_type == 'PUT' else 'rast'} ceny
    → Chcete zvýšiť pravdepodobnosť profitu
    
POZOR: Roll zvyšuje celkovú investíciu o ${best['cost']:.2f}
"""
        elif best['action'].startswith('📅'):
            recommendation += """
Roll na dlhšiu expiráciu je výhodný ak:
    → Trend je správny, ale potrebujete viac času
    → Theta vám páli aktuálnu pozíciu
    
POZOR: Dlhšia expirácia = vyššia cena opcie
"""
        
        self.roll_detail_text.delete(1.0, tk.END)
        self.roll_detail_text.insert(tk.END, recommendation)
    
    def on_roll_scenario_select(self, event):
        """Handler pre výber roll scenára"""
        selection = self.roll_tree.selection()
        if not selection:
            return
        
        item = self.roll_tree.item(selection[0])
        values = item['values']
        
        # Nájdi scenár podľa akcie
        action = values[0]
        for s in self.roll_scenarios:
            if s['action'] == action:
                detail = f"""
VYBRANÝ SCENÁR: {s['action']}
{'='*50}

Nový Strike:    {s['new_strike']}
Nová Expirácia: {s['new_expiry']}
Doplatok:       ${s['cost']:.2f}
Celkový Cost:   ${s['total_cost']:.2f}
Break-Even:     {f"${s['break_even']:.2f}" if isinstance(s['break_even'], (int, float)) else s['break_even']}
P(Profit):      {s['prob_profit']:.1f}%
Skóre:          {s['score']:.1f}

📝 {s['note']}
"""
                self.roll_detail_text.delete(1.0, tk.END)
                self.roll_detail_text.insert(tk.END, detail)
                break
    
    def create_scenarios_tab(self, parent):
        """Záložka pre scenárovú analýzu"""
        # === Info panel ===
        info_frame = ttk.LabelFrame(parent, text="Vybraná stratégia", padding=10)
        info_frame.pack(fill='x', padx=10, pady=10)
        
        self.scenario_info_label = ttk.Label(info_frame, text="Najprv nájdite hedge alebo spustite optimalizáciu")
        self.scenario_info_label.pack(fill='x')
        
        # Tlačidlá
        btn_frame = ttk.Frame(info_frame)
        btn_frame.pack(fill='x', pady=5)
        
        ttk.Button(btn_frame, text="📊 GENEROVAŤ SCENÁRE", command=self.generate_scenarios).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="📁 Export", command=self.export_scenarios).pack(side='left', padx=5)
        
        # === P/L Matica ===
        matrix_frame = ttk.LabelFrame(parent, text="P/L Matica (Cena × Čas)", padding=10)
        matrix_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Treeview pre maticu
        self.matrix_tree = ttk.Treeview(matrix_frame, show='headings', height=8)
        self.matrix_tree.pack(fill='both', expand=True)
        
        # === Legendy ===
        legend_frame = ttk.Frame(parent)
        legend_frame.pack(fill='x', padx=10, pady=5)
        
        # Farebné legendy
        ttk.Label(legend_frame, text="Legenda:", font=('Arial', 9, 'bold')).pack(side='left', padx=5)
        
        profit_label = tk.Label(legend_frame, text="  PROFIT  ", bg='#90EE90')
        profit_label.pack(side='left', padx=5)
        
        neutral_label = tk.Label(legend_frame, text="  NEUTRAL  ", bg='#FFFACD')
        neutral_label.pack(side='left', padx=5)
        
        loss_label = tk.Label(legend_frame, text="  LOSS  ", bg='#FFB6C1')
        loss_label.pack(side='left', padx=5)
        
        # === Scenáre text ===
        scenarios_text_frame = ttk.LabelFrame(parent, text="Detaily scenárov", padding=10)
        scenarios_text_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.scenarios_text = scrolledtext.ScrolledText(scenarios_text_frame, height=10, font=('Courier', 9))
        self.scenarios_text.pack(fill='both', expand=True)
    
    def run_optimization(self):
        """Spustí margin optimalizáciu"""
        self.optimize_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.stop_optimization_flag = False
        self.opt_status_label.config(text="Optimalizujem...")
        
        # Spusti progress bar
        self.opt_progress.start(10)
        
        # Vyčisti log
        self.opt_log_text.delete(1.0, tk.END)
        self.log_optimization("🚀 Spúšťam optimalizáciu...")
        
        # Vyčisti tabuľku
        for item in self.alt_tree.get_children():
            self.alt_tree.delete(item)
        
        def run():
            cmd = [
                'python', 'scripts/hedge_calculator.py',
                '--symbol', self.symbol_var.get(),
                '--min-premium', self.min_premium_var.get(),
                '--port', self.port_var.get(),
                '--option-type', self.option_type_var.get(),
                '--optimize',
                '--broker', self.broker_var.get(),
                '--dte-offsets', self.dte_offsets_var.get(),
            ]
            
            # Max margin
            max_margin = self.max_margin_var.get()
            if max_margin and float(max_margin) > 0:
                cmd.extend(['--max-margin', max_margin])
            
            # Min ROI
            min_roi = self.min_roi_var.get()
            if min_roi and float(min_roi) > 0:
                cmd.extend(['--min-roi', min_roi])
            
            # Expirácia
            if self.short_expiry_var.get():
                cmd.extend(['--short-expiry', self.short_expiry_var.get()])
            
            self.root.after(0, lambda: self.log_optimization(f"📋 Príkaz: {' '.join(cmd)}"))
            
            try:
                self.optimization_process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd='/home/narbon/Aplikácie/tws-webapp',
                    env={**os.environ, 'PATH': '/home/narbon/Aplikácie/tws-webapp/venv/bin:' + os.environ.get('PATH', '')}
                )
                
                output_lines = []
                
                # Čítaj výstup riadok po riadku
                for line in iter(self.optimization_process.stdout.readline, ''):
                    if self.stop_optimization_flag:
                        self.optimization_process.terminate()
                        self.root.after(0, lambda: self.log_optimization("⛔ Optimalizácia zastavená používateľom"))
                        break
                    
                    output_lines.append(line)
                    
                    # Logovanie priebežného výstupu
                    if "[OPT]" in line:
                        # Odstráň prefix [OPT] pre prehľadnejšie zobrazenie
                        clean_line = line.replace("[OPT]", "").strip()
                        if "===" in line:
                            self.root.after(0, lambda l=clean_line: self.log_optimization(f"📊 {l}"))
                        elif "✓" in line:
                            self.root.after(0, lambda l=clean_line: self.log_optimization(f"✅ {l}"))
                        elif "SKIP" in line:
                            self.root.after(0, lambda l=clean_line: self.log_optimization(f"⏭️ {l}"))
                        elif "Hľadám" in line or "Analyzujem" in line:
                            self.root.after(0, lambda l=clean_line: self.log_optimization(f"🔍 {l}"))
                        else:
                            self.root.after(0, lambda l=clean_line: self.log_optimization(f"ℹ️ {l}"))
                    elif "error" in line.lower() or "chyba" in line.lower():
                        self.root.after(0, lambda l=line.strip(): self.log_optimization(f"❌ {l}"))
                
                self.optimization_process.wait()
                output = ''.join(output_lines)
                
                if not self.stop_optimization_flag:
                    self.root.after(0, lambda: self.display_optimization_result(output))
                else:
                    self.root.after(0, lambda: self.finish_optimization())
                    
            except Exception as e:
                self.root.after(0, lambda: self.log_optimization(f"❌ Chyba: {e}"))
                self.root.after(0, lambda: self.display_optimization_result(f"Chyba: {e}"))
        
        threading.Thread(target=run, daemon=True).start()
    
    def stop_optimization(self):
        """Zastaví prebiehajúcu optimalizáciu"""
        self.stop_optimization_flag = True
        self.log_optimization("⏳ Zastavujem optimalizáciu...")
        if self.optimization_process:
            try:
                self.optimization_process.terminate()
            except:
                pass
    
    def finish_optimization(self):
        """Ukončí optimalizáciu - reset UI"""
        self.opt_progress.stop()
        self.optimize_btn.config(state='normal')
        self.stop_btn.config(state='disabled')
        self.opt_status_label.config(text="Zastavené")
    
    def log_optimization(self, message):
        """Pridá správu do logu optimalizácie"""
        self.opt_log_text.insert(tk.END, f"{message}\n")
        self.opt_log_text.see(tk.END)  # Scrolluj na koniec
    
    def display_optimization_result(self, output):
        """Zobrazí výsledok optimalizácie"""
        self.opt_progress.stop()
        self.optimize_btn.config(state='normal')
        self.stop_btn.config(state='disabled')
        self.opt_status_label.config(text="Hotovo")
        
        # Nájdi JSON v outpute
        try:
            json_start = output.rfind('{')
            if json_start >= 0:
                json_str = output[json_start:]
                result = json.loads(json_str)
                self.last_result = result
                
                if result.get('success') and result.get('alternatives'):
                    self.alternatives = result['alternatives']
                    self.log_optimization(f"✅ Nájdených {len(self.alternatives)} alternatív")
                    
                    # Naplň tabuľku
                    for alt in self.alternatives:
                        self.alt_tree.insert('', 'end', values=(
                            f"+{alt.get('dteOffset', 0)}d",
                            alt.get('longStrike', ''),
                            f"${alt.get('margin', 0):.0f}",
                            f"${alt.get('netCredit', 0):.2f}",
                            f"{alt.get('weeklyROI', 0):.2f}%",
                            f"{alt.get('thetaAdjustedWeeklyROI', 0):.2f}%",
                            alt.get('spreadType', ''),
                        ))
                    
                    # Sumár
                    self.update_summary()
                    
                    # Aktualizuj info pre scenáre
                    self.update_scenario_info()
                    
                elif result.get('success'):
                    # Štandardný výsledok bez alternatív
                    self.last_result = result
                    self.log_optimization("ℹ️ Výsledok bez alternatív")
                    self.update_scenario_info()
                else:
                    error_msg = result.get('error', 'Neznáma chyba')
                    self.log_optimization(f"❌ {error_msg}")
                    self.show_recommendations(error_msg)
                    messagebox.showerror("Chyba", error_msg)
            else:
                # Žiadny JSON = neboli nájdené žiadne výsledky
                self.log_optimization("❌ Žiadne výsledky nenájdené")
                self.show_recommendations("no_results")
        except json.JSONDecodeError as e:
            self.log_optimization(f"❌ Chyba parsovania: {e}")
            messagebox.showerror("Chyba", f"Nepodarilo sa parsovať výsledok: {e}")
    
    def show_recommendations(self, error_context):
        """Zobrazí odporúčania pri neúspešnom vyhľadávaní"""
        recommendations = []
        
        min_premium = float(self.min_premium_var.get() or 0.7)
        max_margin = float(self.max_margin_var.get() or 5000)
        min_roi = float(self.min_roi_var.get() or 3.0)
        
        if "premium" in error_context.lower() or "no_results" in error_context.lower():
            recommendations.append(f"💡 Skúste znížiť Min Premium z ${min_premium:.2f} na ${min_premium * 0.7:.2f}")
        
        if "margin" in error_context.lower() or "no_results" in error_context.lower():
            recommendations.append(f"💡 Skúste zvýšiť Max Margin z ${max_margin:.0f} na ${max_margin * 1.5:.0f}")
        
        if "roi" in error_context.lower() or "no_results" in error_context.lower():
            recommendations.append(f"💡 Skúste znížiť Min ROI z {min_roi:.1f}% na {min_roi * 0.6:.1f}%")
        
        if not recommendations:
            recommendations.append("💡 Skúste zmeniť symbol alebo počkať na vhodnejšie trhové podmienky")
            recommendations.append("💡 Overte, že máte načítané expirácie pomocou tlačidla '🔄 Načítať expirácie'")
        
        self.log_optimization("\n📋 ODPORÚČANIA:")
        for rec in recommendations:
            self.log_optimization(rec)
    
    def update_summary(self):
        """Aktualizuje sumárny panel"""
        self.summary_text.delete(1.0, tk.END)
        
        if not self.alternatives:
            return
        
        # Najlepší ROI
        best_roi = max(self.alternatives, key=lambda x: x.get('thetaAdjustedWeeklyROI', 0))
        
        # Najnižší margin
        best_margin = min(self.alternatives, key=lambda x: x.get('margin', float('inf')))
        
        summary = f"""
🏆 NAJLEPŠÍ ROI:     DTE +{best_roi.get('dteOffset', 0)}d | Margin ${best_roi.get('margin', 0):.0f} | ROI {best_roi.get('thetaAdjustedWeeklyROI', 0):.2f}%
💰 NAJNIŽŠÍ MARGIN:  DTE +{best_margin.get('dteOffset', 0)}d | Margin ${best_margin.get('margin', 0):.0f} | ROI {best_margin.get('thetaAdjustedWeeklyROI', 0):.2f}%
"""
        self.summary_text.insert(tk.END, summary)
    
    def on_alternative_select(self, event):
        """Handler pre výber alternatívy v tabuľke"""
        selection = self.alt_tree.selection()
        if selection:
            # Môžete tu pridať ďalšie akcie
            pass
    
    def update_scenario_info(self):
        """Aktualizuje info pre scenárovú analýzu"""
        if self.last_result and self.last_result.get('success'):
            r = self.last_result
            info = f"Symbol: {r.get('symbol', '')} | "
            info += f"Short: {r.get('shortLeg', {}).get('strike', '')} @ ${r.get('shortLeg', {}).get('premium', 0):.2f} | "
            info += f"Long: {r.get('longLeg', {}).get('strike', '')} @ ${r.get('longLeg', {}).get('premium', 0):.2f} | "
            info += f"Net Credit: ${r.get('strategy', {}).get('netCredit', 0):.2f}"
            self.scenario_info_label.config(text=info)
    
    def generate_scenarios(self):
        """Generuje scenárovú analýzu"""
        if not self.last_result or not self.last_result.get('success'):
            messagebox.showwarning("Upozornenie", "Najprv nájdite hedge alebo spustite optimalizáciu")
            return
        
        if not SCENARIO_AVAILABLE:
            messagebox.showerror("Chyba", "Modul scenario_simulator nie je dostupný")
            return
        
        try:
            simulator = ScenarioSimulator()
            
            # Priprav stratégiu pre simulátor
            strategy = self.last_result
            
            # Generuj scenáre
            price_scenarios = simulator.simulate_price_move(strategy)
            time_scenarios = simulator.simulate_time_decay(strategy)
            combined = simulator.simulate_combined(strategy)
            
            self.scenarios = {
                'price': price_scenarios,
                'time': time_scenarios,
                'combined': combined,
            }
            
            # Zobraz maticu
            self.display_matrix(combined)
            
            # Zobraz detaily
            self.display_scenario_details(price_scenarios, time_scenarios)
            
        except Exception as e:
            messagebox.showerror("Chyba", f"Chyba pri generovaní scenárov: {e}")
    
    def display_matrix(self, combined):
        """Zobrazí P/L maticu"""
        # Vyčisti
        self.matrix_tree.delete(*self.matrix_tree.get_children())
        
        price_changes = combined.get('priceChanges', [-5, -2, 0, 2, 5])
        matrix = combined.get('matrix', [])
        
        # Nastav stĺpce
        columns = ['DTE'] + [f"{p:+.0f}%" for p in price_changes]
        self.matrix_tree['columns'] = columns
        
        for col in columns:
            self.matrix_tree.heading(col, text=col)
            self.matrix_tree.column(col, width=80, anchor='center')
        
        # Pridaj riadky
        for row in matrix:
            values = [row.get('shortDTE', '')]
            for scenario in row.get('scenarios', []):
                pnl = scenario.get('pnl', 0)
                values.append(f"${pnl:+.0f}")
            
            item = self.matrix_tree.insert('', 'end', values=values)
            
            # Tu by sme mohli farbiť bunky, ale Treeview to priamo nepodporuje
    
    def display_scenario_details(self, price_scenarios, time_scenarios):
        """Zobrazí detaily scenárov"""
        self.scenarios_text.delete(1.0, tk.END)
        
        text = "=== SCENÁRE - POHYB CENY ===\n"
        text += f"Aktuálna cena: ${price_scenarios.get('originalPrice', 0):.2f}\n\n"
        
        for s in price_scenarios.get('scenarios', []):
            text += f"  {s.get('priceChange', 0):+.0f}% → ${s.get('newPrice', 0):.2f}: "
            text += f"P/L ${s.get('pnl', 0):+.2f}\n"
        
        text += "\n=== SCENÁRE - ČASOVÝ ROZPAD ===\n\n"
        
        for s in time_scenarios.get('scenarios', []):
            text += f"  +{s.get('daysForward', 0)}d (DTE {s.get('shortDTE', 0)}): "
            text += f"P/L ${s.get('pnl', 0):+.2f}\n"
        
        self.scenarios_text.insert(tk.END, text)
    
    def export_results(self):
        """Exportuje výsledky do Excel"""
        if not self.last_result:
            messagebox.showwarning("Upozornenie", "Žiadne výsledky na export")
            return
        
        if not EXPORT_AVAILABLE:
            messagebox.showerror("Chyba", "Modul export_utils nie je dostupný")
            return
        
        try:
            # Vyber adresár
            export_dir = filedialog.askdirectory(title="Vyber adresár pre export")
            if not export_dir:
                return
            
            # Priprav scenáre ak existujú
            scenarios_data = None
            if self.scenarios:
                scenarios_data = {
                    'priceScenarios': self.scenarios.get('price', {}).get('scenarios', []),
                    'timeScenarios': self.scenarios.get('time', {}).get('scenarios', []),
                    'combinedMatrix': self.scenarios.get('combined', {}),
                }
            
            # Export
            result = export_strategy(
                strategy=self.last_result,
                scenarios=scenarios_data,
                alternatives=self.alternatives if self.alternatives else None,
                margin_info=self.last_result.get('marginInfo'),
                output_dir=export_dir,
                format='both'
            )
            
            if result['success']:
                messagebox.showinfo("Export", f"Exportované do:\n" + "\n".join(result['files']))
            else:
                messagebox.showerror("Chyba", result.get('error', 'Neznáma chyba'))
                
        except Exception as e:
            messagebox.showerror("Chyba", f"Chyba pri exporte: {e}")
    
    def export_scenarios(self):
        """Exportuje scenáre"""
        if not self.scenarios:
            messagebox.showwarning("Upozornenie", "Najprv vygenerujte scenáre")
            return
        
        self.export_results()
    
    def update_calc_status(self, text):
        """Bezpečne aktualizuje status label v kalkulátore"""
        if hasattr(self, 'calc_status_label'):
            self.calc_status_label.config(text=text)
    
    def load_expiries(self):
        """Načíta dostupné expirácie z TWS"""
        self.update_calc_status("Načítavam expirácie...")
        
        # Log do optimizer logu ak existuje
        if hasattr(self, 'opt_log_text'):
            self.log_optimization("🔄 Načítavam expirácie z TWS...")
        
        # Použij správny option type
        right = 'C' if self.option_type_var.get() == 'CALL' else 'P'
        
        def run():
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_load_expiries.py')
                result = subprocess.run(
                    ['python3', script_path, str(self.port_var.get()), self.symbol_var.get(), right], 
                    capture_output=True, text=True, timeout=45,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    expiries = result.stdout.strip().split(',')
                    self.root.after(0, lambda: self.update_expiry_combos(expiries))
                else:
                    error_msg = result.stderr.strip() if result.stderr else "Neznáma chyba"
                    self.root.after(0, lambda: self.handle_expiry_error(error_msg))
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self.handle_expiry_error("Timeout - TWS neodpovedá"))
            except Exception as e:
                self.root.after(0, lambda: self.handle_expiry_error(str(e)))
        
        threading.Thread(target=run, daemon=True).start()
    
    def handle_expiry_error(self, error_msg):
        """Spracuje chybu pri načítaní expirácií"""
        self.update_calc_status("Chyba načítania expirácií")
        
        # Log do optimizer logu ak existuje
        if hasattr(self, 'opt_log_text'):
            self.log_optimization(f"❌ Chyba: {error_msg}")
            self.log_optimization("💡 Skontrolujte:")
            self.log_optimization("   - Je TWS/IB Gateway spustený?")
            self.log_optimization(f"   - Je port {self.port_var.get()} správny?")
            self.log_optimization("   - Je povolené API pripojenie v TWS?")
        
        messagebox.showerror("Chyba načítania expirácií", 
            f"Nepodarilo sa načítať expirácie.\n\n"
            f"Chyba: {error_msg}\n\n"
            f"Skontrolujte:\n"
            f"• Je TWS/IB Gateway spustený?\n"
            f"• Je port {self.port_var.get()} správny? (7496=live, 7497=paper)\n"
            f"• Je povolené API pripojenie v TWS nastaveniach?")
    
    def update_expiry_combos(self, expiries):
        """Aktualizuje combobox s expiráciami"""
        # Uložíme expirácie pre interaktívny optimizer
        self.available_expiries = expiries
        
        # Kalkulátor combo
        if hasattr(self, 'calc_short_expiry_combo'):
            self.calc_short_expiry_combo['values'] = expiries
        if hasattr(self, 'calc_long_expiry_combo'):
            self.calc_long_expiry_combo['values'] = expiries
        
        # Monitor combo
        if hasattr(self, 'monitor_expiry_combo'):
            self.monitor_expiry_combo['values'] = expiries
        
        # Margin Optimizer combo
        if hasattr(self, 'opt_short_expiry_combo'):
            self.opt_short_expiry_combo['values'] = expiries
        
        # Nastav defaultné hodnoty
        if len(expiries) >= 2:
            self.calc_short_expiry_var.set(expiries[0])
        if len(expiries) >= 5:
            self.calc_long_expiry_var.set(expiries[4])
        elif len(expiries) >= 2:
            self.calc_long_expiry_var.set(expiries[-1])
        
        self.update_calc_status(f"Načítaných {len(expiries)} expirácií")
        
        # Log aj do optimizer logu ak existuje
        if hasattr(self, 'opt_log_text'):
            self.log_optimization(f"📅 Načítaných {len(expiries)} expirácií: {', '.join(expiries[:5])}...")
    
    def find_hedge(self):
        """Spustí hľadanie hedge"""
        self.find_btn.config(state='disabled')
        opt_type = self.option_type_var.get()
        self.status_label.config(text=f"Hľadám {opt_type} hedge... (môže trvať 2-3 min)")
        self.hedge_result_text.delete(1.0, tk.END)
        
        def run():
            cmd = [
                'python', 'scripts/hedge_calculator.py',
                '--symbol', self.symbol_var.get(),
                '--min-premium', self.min_premium_var.get(),
                '--port', self.port_var.get(),
                '--option-type', self.option_type_var.get()
            ]
            
            if self.short_expiry_var.get():
                cmd.extend(['--short-expiry', self.short_expiry_var.get()])
            if self.long_expiry_var.get():
                cmd.extend(['--long-expiry', self.long_expiry_var.get()])
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                       cwd='/home/narbon/Aplikácie/tws-webapp',
                                       env={**os.environ, 'PATH': '/home/narbon/Aplikácie/tws-webapp/venv/bin:' + os.environ.get('PATH', '')})
                
                output = result.stdout + result.stderr
                self.root.after(0, lambda: self.display_hedge_result(output))
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self.display_hedge_result("Timeout - skúste znova"))
            except Exception as e:
                self.root.after(0, lambda: self.display_hedge_result(f"Chyba: {e}"))
        
        threading.Thread(target=run, daemon=True).start()
    
    def display_hedge_result(self, output):
        """Zobrazí výsledok hľadania hedge"""
        self.find_btn.config(state='normal')
        self.status_label.config(text="Hotovo")
        
        self.hedge_result_text.delete(1.0, tk.END)
        
        # Nájdi JSON v outpute
        try:
            json_start = output.rfind('{')
            if json_start >= 0:
                json_str = output[json_start:]
                self.last_result = json.loads(json_str)
                
                if self.last_result.get('success'):
                    # Formatovaný výstup
                    r = self.last_result
                    formatted = f"""
╔══════════════════════════════════════════════════════════╗
║  HEDGE NÁJDENÝ pre {r['symbol']}                              
╠══════════════════════════════════════════════════════════╣
║  Aktuálna cena: ${r['currentPrice']:.2f}
╠══════════════════════════════════════════════════════════╣
║  SHORT PUT (SELL):
║    Strike: ${r['shortLeg']['strike']}
║    Expirácia: {r['shortLeg']['expiry']}
║    Premium: ${r['shortLeg']['premium']:.2f}
║    Delta: {r['shortLeg']['delta']:.4f}
║    Theta: {r['shortLeg']['theta']:.4f}
╠══════════════════════════════════════════════════════════╣
║  LONG PUT (BUY):
║    Strike: ${r['longLeg']['strike']}
║    Expirácia: {r['longLeg']['expiry']}
║    Premium: ${r['longLeg']['premium']:.2f}
║    Delta: {r['longLeg']['delta']:.4f}
╠══════════════════════════════════════════════════════════╣
║  STRATÉGIA:
║    Net Credit: ${r['strategy']['netCredit']:.2f} (${r['strategy']['maxProfit']:.0f}/contract)
║    Max Loss: ${r['strategy']['maxLoss']:.0f}
║    Breakeven: ${r['strategy']['breakeven']:.2f}
║    Spread Width: ${r['strategy']['spreadWidth']}
╠══════════════════════════════════════════════════════════╣
║  EXIT PLAN:
║    📈 50% Profit: {r['symbol']} > ${r['exitPlan']['profit50']['whenUnderlyingAbove']:.2f}
║       → Kúp späť za ${r['exitPlan']['profit50']['buyBackSpreadAt']:.2f}
║    🔄 Roll: Keď delta = {r['exitPlan']['roll']['triggerDelta']}
║    🛑 Max Loss: {r['symbol']} < ${r['exitPlan']['maxLoss']['whenUnderlyingBelow']}
╚══════════════════════════════════════════════════════════╝
"""
                    self.hedge_result_text.insert(tk.END, formatted)
                    
                    # Aktualizuj premenné pre ďalšie záložky
                    self.short_strike_var.set(str(r['shortLeg']['strike']))
                    if r['shortLeg'].get('iv'):
                        self.iv_var.set(str(round(r['shortLeg']['iv'], 4)))
                else:
                    self.hedge_result_text.insert(tk.END, f"Nepodarilo sa nájsť hedge:\n{r.get('error', 'Neznáma chyba')}")
        except json.JSONDecodeError:
            pass
        
        # Zobraz aj raw output
        self.hedge_result_text.insert(tk.END, "\n\n--- Raw Output ---\n")
        self.hedge_result_text.insert(tk.END, output)
    
    def calculate_exit_prices(self):
        """Vypočíta exit ceny lokálne pomocou Black-Scholes"""
        if not SCIPY_AVAILABLE:
            messagebox.showerror("Chyba", "scipy nie je nainštalované")
            return
        
        try:
            strike = float(self.short_strike_var.get())
            iv = float(self.iv_var.get())
            expiry = self.short_expiry_var.get()
            is_call = self.option_type_var.get() == "CALL"
            
            if not expiry:
                messagebox.showerror("Chyba", "Zadajte expiráciu")
                return
            
            # Dni do expirácie
            exp_date = datetime.strptime(expiry, '%Y%m%d').date()
            T = (exp_date - date.today()).days / 365
            r = float(self.rate_var.get())
            
            # Vyčisti tabuľku
            for item in self.exit_tree.get_children():
                self.exit_tree.delete(item)
            
            # Pre CALL: delta je kladná (0 až 1), pre PUT záporná (-1 až 0)
            if is_call:
                # CALL: Short call riziko keď cena rastie a delta sa blíži k 1
                delta_targets = [
                    (0.15, ""),
                    (0.20, ""),
                    (0.25, "⚠️ Pozor"),
                    (0.30, "🔄 ROLL"),
                    (0.40, ""),
                    (0.50, "🛑 STOP")
                ]
            else:
                # PUT: Short put riziko keď cena klesá a delta sa blíži k -1
                delta_targets = [
                    (-0.15, ""),
                    (-0.20, ""),
                    (-0.25, "⚠️ Pozor"),
                    (-0.30, "🔄 ROLL"),
                    (-0.40, ""),
                    (-0.50, "🛑 STOP")
                ]
            
            results = []
            for target_delta, action in delta_targets:
                S_target = self.find_underlying_for_delta(target_delta, strike, T, r, iv, is_call)
                if S_target:
                    opt_price = self.get_option_price(S_target, strike, T, r, iv, is_call)
                    self.exit_tree.insert('', 'end', values=(
                        f"{target_delta:.2f}",
                        f"${S_target:.2f}",
                        f"${opt_price:.2f}",
                        action
                    ))
                    results.append((target_delta, S_target, opt_price))
            
            # Odporúčania
            self.recommendations_text.delete(1.0, tk.END)
            
            if is_call:
                alert_delta, roll_delta, stop_delta = 0.25, 0.30, 0.50
                direction = ">"  # CALL riziko keď cena rastie
            else:
                alert_delta, roll_delta, stop_delta = -0.25, -0.30, -0.50
                direction = "<"  # PUT riziko keď cena klesá
            
            alert_price = self.find_underlying_for_delta(alert_delta, strike, T, r, iv, is_call)
            roll_price = self.find_underlying_for_delta(roll_delta, strike, T, r, iv, is_call)
            stop_price = self.find_underlying_for_delta(stop_delta, strike, T, r, iv, is_call)
            
            opt_type = self.option_type_var.get()
            rec = f"""
PRE NASTAVENIE V BROKERI ({self.symbol_var.get()} {opt_type}):
═══════════════════════════════════════════════
⚠️  ALERT:      Keď {self.symbol_var.get()} {direction} ${alert_price:.2f}
🔄 ROLL/CLOSE: Keď {self.symbol_var.get()} {direction} ${roll_price:.2f}
🛑 STOP LOSS:  Keď {self.symbol_var.get()} {direction} ${stop_price:.2f}
═══════════════════════════════════════════════
"""
            self.recommendations_text.insert(tk.END, rec)
            
        except ValueError as e:
            messagebox.showerror("Chyba", f"Neplatné hodnoty: {e}")
    
    def black_scholes_put_price(self, S, K, T, r, sigma):
        """Black-Scholes cena PUT"""
        if T <= 0:
            return max(K - S, 0)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    
    def black_scholes_call_price(self, S, K, T, r, sigma):
        """Black-Scholes cena CALL"""
        if T <= 0:
            return max(S - K, 0)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    
    def black_scholes_delta_put(self, S, K, T, r, sigma):
        """Black-Scholes delta PUT"""
        if T <= 0:
            return -1.0 if S < K else 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm.cdf(d1) - 1
    
    def black_scholes_delta_call(self, S, K, T, r, sigma):
        """Black-Scholes delta CALL"""
        if T <= 0:
            return 1.0 if S > K else 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm.cdf(d1)
    
    def find_underlying_for_delta(self, target_delta, K, T, r, sigma, is_call=False):
        """Nájde cenu podkladu pre cieľovú deltu"""
        try:
            if is_call:
                def delta_diff(S):
                    return self.black_scholes_delta_call(S, K, T, r, sigma) - target_delta
                return brentq(delta_diff, K * 0.9, K * 1.3)
            else:
                def delta_diff(S):
                    return self.black_scholes_delta_put(S, K, T, r, sigma) - target_delta
                return brentq(delta_diff, K * 0.7, K * 1.1)
        except:
            return None
    
    def get_option_price(self, S, K, T, r, sigma, is_call=False):
        """Vráti cenu opcie podľa typu"""
        if is_call:
            return self.black_scholes_call_price(S, K, T, r, sigma)
        else:
            return self.black_scholes_put_price(S, K, T, r, sigma)
    
    def load_from_tws(self):
        """Načíta IV a aktuálne dáta z TWS"""
        self.status_label.config(text="Načítavam z TWS...")
        
        def run():
            cmd = [
                'python', 'scripts/delta_price_calc.py',
                '--symbol', self.symbol_var.get(),
                '--strike', self.short_strike_var.get(),
                '--expiry', self.short_expiry_var.get(),
                '--port', self.port_var.get()
            ]
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                       cwd='/home/narbon/Aplikácie/tws-webapp',
                                       env={**os.environ, 'PATH': '/home/narbon/Aplikácie/tws-webapp/venv/bin:' + os.environ.get('PATH', '')})
                
                if result.returncode == 0:
                    try:
                        json_start = result.stdout.rfind('{')
                        if json_start >= 0:
                            data = json.loads(result.stdout[json_start:])
                            if data.get('iv'):
                                self.root.after(0, lambda: self.iv_var.set(str(data['iv'])))
                            self.root.after(0, lambda: self.calculate_exit_prices())
                    except:
                        pass
                
                self.root.after(0, lambda: self.status_label.config(text="Hotovo"))
            except Exception as e:
                self.root.after(0, lambda: self.status_label.config(text=f"Chyba: {e}"))
        
        threading.Thread(target=run, daemon=True).start()
    
    def check_position(self):
        """Skontroluje aktuálny stav pozície"""
        self.monitor_result_text.delete(1.0, tk.END)
        self.monitor_result_text.insert(tk.END, "Kontrolujem pozíciu...\n")
        
        def run():
            cmd = [
                'python', 'scripts/position_monitor.py',
                '--symbol', self.symbol_var.get(),
                '--short-strike', self.short_strike_var.get(),
                '--short-expiry', self.short_expiry_var.get(),
                '--port', self.port_var.get(),
                '--once'
            ]
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                       cwd='/home/narbon/Aplikácie/tws-webapp',
                                       env={**os.environ, 'PATH': '/home/narbon/Aplikácie/tws-webapp/venv/bin:' + os.environ.get('PATH', '')})
                
                output = result.stdout + result.stderr
                self.root.after(0, lambda: self.display_monitor_result(output))
            except Exception as e:
                self.root.after(0, lambda: self.display_monitor_result(f"Chyba: {e}"))
        
        threading.Thread(target=run, daemon=True).start()
    
    def display_monitor_result(self, output):
        """Zobrazí výsledok monitoringu"""
        self.monitor_result_text.delete(1.0, tk.END)
        self.monitor_result_text.insert(tk.END, output)
    
    def save_results(self):
        """Uloží výsledky do súboru"""
        if self.last_result:
            filename = f"hedge_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w') as f:
                json.dump(self.last_result, f, indent=2)
            messagebox.showinfo("Uložené", f"Výsledky uložené do {filename}")
    
    def clear_results(self):
        """Vymaže výsledky"""
        self.full_results_text.delete(1.0, tk.END)
        self.last_result = None
    
    def create_status_bar(self):
        """Vytvorí status bar s indikátorom pripojenia"""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill='x', padx=5, pady=2)
        
        # Indikátor pripojenia
        self.conn_indicator = tk.Label(status_frame, text="●", font=('Arial', 14), fg='gray')
        self.conn_indicator.pack(side='left', padx=2)
        
        self.conn_label = ttk.Label(status_frame, text="Nepripojené", font=('Arial', 9))
        self.conn_label.pack(side='left', padx=5)
        
        ttk.Button(status_frame, text="🔄 Test pripojenia", command=self.check_connection).pack(side='left', padx=10)
        
        # Pravá strana - port
        ttk.Label(status_frame, text="Port:").pack(side='right', padx=2)
        port_combo = ttk.Combobox(status_frame, textvariable=self.port_var, values=["7496", "7497"], width=6)
        port_combo.pack(side='right', padx=2)
        port_combo.bind('<<ComboboxSelected>>', lambda e: self.check_connection())
    
    def create_connection_tab(self, parent):
        """Záložka pre kontrolu pripojenia"""
        frame = ttk.LabelFrame(parent, text="Stav pripojenia k TWS", padding=15)
        frame.pack(fill='x', padx=10, pady=10)
        
        # Info o pripojení
        self.conn_info_text = tk.Text(frame, height=10, font=('Courier', 11), state='disabled')
        self.conn_info_text.pack(fill='x', pady=10)
        
        # Tlačidlá
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill='x', pady=10)
        
        ttk.Button(btn_frame, text="🔄 Otestovať pripojenie", command=self.check_connection).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="📋 Načítať expirácie", command=self.load_expiries).pack(side='left', padx=5)
        
        # Návod
        help_frame = ttk.LabelFrame(parent, text="Návod", padding=10)
        help_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        help_text = """
PRED POUŽITÍM:
1. Spustite Trader Workstation (TWS) alebo IB Gateway
2. Povoľte API pripojenie v TWS:
   Edit → Global Configuration → API → Settings
   ✓ Enable ActiveX and Socket Clients
   ✓ Socket port: 7496 (Live) alebo 7497 (Paper)
   ✓ Read-Only API: Áno (bezpečnejšie)

3. Uistite sa, že máte OPRA Market Data subscription
   (potrebné pre options greeks - delta, theta)

PORTY:
• 7496 - TWS Live Trading
• 7497 - TWS Paper Trading

TIP: Pre testovanie používajte Paper Trading (port 7497)
"""
        help_label = ttk.Label(help_frame, text=help_text, font=('Arial', 10), justify='left')
        help_label.pack(fill='both', expand=True)
    
    def check_connection(self):
        """Otestuje pripojenie k TWS"""
        self.conn_indicator.config(fg='yellow')
        self.conn_label.config(text="Testujem...")
        
        def run():
            port = self.port_var.get()
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'tws_check_connection.py')
                result = subprocess.run(
                    ['python3', script_path, str(port)], 
                    capture_output=True, text=True, timeout=15,
                    cwd='/home/narbon/Aplikácie/tws-webapp'
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        info = json.loads(result.stdout.strip())
                        self.root.after(0, lambda: self.update_connection_status(info))
                    except:
                        self.root.after(0, lambda: self.update_connection_status({'connected': False, 'error': result.stdout + result.stderr}))
                else:
                    self.root.after(0, lambda: self.update_connection_status({'connected': False, 'error': result.stderr}))
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self.update_connection_status({'connected': False, 'error': 'Timeout - TWS neodpovedá'}))
            except Exception as e:
                self.root.after(0, lambda: self.update_connection_status({'connected': False, 'error': str(e)}))
        
        threading.Thread(target=run, daemon=True).start()
    
    def update_connection_status(self, info):
        """Aktualizuje zobrazenie stavu pripojenia"""
        self.connected = info.get('connected', False)
        self.connection_info = info
        
        if self.connected:
            self.conn_indicator.config(fg='green')
            self.conn_label.config(text=f"Pripojené k TWS (port {info.get('port', '?')})")
            
            # Aktualizuj info v záložke
            if hasattr(self, 'conn_info_text'):
                self.conn_info_text.config(state='normal')
                self.conn_info_text.delete(1.0, tk.END)
                
                text = f"""
✅ PRIPOJENIE ÚSPEŠNÉ
═══════════════════════════════════════════════
   Host:           127.0.0.1
   Port:           {info.get('port', '?')}
   Server Version: {info.get('serverVersion', '?')}
   Účty:           {', '.join(info.get('accounts', []))}
═══════════════════════════════════════════════
Pripravené na použitie!
"""
                self.conn_info_text.insert(tk.END, text)
                self.conn_info_text.config(state='disabled')
            
            # Automaticky načítaj expirácie
            self.load_expiries()
        else:
            self.conn_indicator.config(fg='red')
            self.conn_label.config(text="Nepripojené")
            
            if hasattr(self, 'conn_info_text'):
                self.conn_info_text.config(state='normal')
                self.conn_info_text.delete(1.0, tk.END)
                
                text = f"""
❌ PRIPOJENIE ZLYHALO
═══════════════════════════════════════════════
   Port:  {self.port_var.get()}
   Chyba: {info.get('error', 'Neznáma chyba')}
═══════════════════════════════════════════════
Skontrolujte:
1. Je TWS spustený?
2. Je API povolené?
3. Je správny port?
"""
                self.conn_info_text.insert(tk.END, text)
                self.conn_info_text.config(state='disabled')
    
    # ═══════════════════════════════════════════════════════════════════════════
    # ARCHÍV NASTAVENÍ - Ukladanie/Načítavanie stratégií
    # ═══════════════════════════════════════════════════════════════════════════
    
    def load_settings_file(self):
        """Načíta archív nastavení zo súboru"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.saved_strategies = data.get('strategies', {})
                    
                    # Aktualizuj dropdown
                    strategy_names = sorted(self.saved_strategies.keys())
                    self.strategy_combo['values'] = strategy_names
                    
                    # Auto-load poslednej použitej stratégie
                    last_used = data.get('last_used')
                    if last_used and last_used in self.saved_strategies:
                        self.strategy_name_var.set(last_used)
                        self.load_strategy(auto=True)
            else:
                self.saved_strategies = {}
                self.strategy_combo['values'] = []
        except Exception as e:
            print(f"Chyba pri načítavaní nastavení: {e}")
            self.saved_strategies = {}
    
    def save_settings_file(self):
        """Uloží archív nastavení do súboru"""
        try:
            data = {
                'last_used': self.strategy_name_var.get(),
                'strategies': self.saved_strategies
            }
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Chyba", f"Nepodarilo sa uložiť nastavenia:\n{e}")
    
    def save_strategy(self):
        """Uloží aktuálne nastavenia kalkulátora"""
        name = self.strategy_name_var.get().strip()
        if not name:
            name = messagebox.askstring("Názov stratégie", "Zadajte názov pre túto stratégiu:")
            if not name:
                return
            name = name.strip()
        
        # Zber aktuálne hodnoty z kalkulátora
        try:
            strategy = {
                'symbol': self.symbol_var.get(),
                'option_type': self.option_type_var.get(),
                'underlying_price': self.calc_underlying_price_var.get(),
                'short_strike': self.calc_short_strike_var.get(),
                'short_expiry': self.calc_short_expiry_var.get(),
                'short_premium': self.calc_short_premium_var.get(),
                'long_strike': self.calc_long_strike_var.get(),
                'long_expiry': self.calc_long_expiry_var.get(),
                'long_premium': self.calc_long_premium_var.get(),
                'broker': self.broker_var.get(),
                'saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            self.saved_strategies[name] = strategy
            self.strategy_name_var.set(name)
            
            # Aktualizuj dropdown
            strategy_names = sorted(self.saved_strategies.keys())
            self.strategy_combo['values'] = strategy_names
            
            # Aktualizuj archív listbox
            if hasattr(self, 'archive_listbox'):
                self.archive_refresh_list()
            
            self.save_settings_file()
            self.update_calc_status(f"✓ Stratégia '{name}' uložená")
            messagebox.showinfo("Úspech", f"Stratégia '{name}' bola uložená.\n\nCelkom stratégií: {len(self.saved_strategies)}")
            
        except Exception as e:
            messagebox.showerror("Chyba", f"Nepodarilo sa uložiť stratégiu:\n{e}")
    
    def load_strategy(self, auto=False):
        """Načíta vybranú stratégiu do kalkulátora"""
        name = self.strategy_name_var.get().strip()
        if not name:
            messagebox.showwarning("Chyba", "Vyberte stratégiu zo zoznamu")
            return
        
        if name not in self.saved_strategies:
            messagebox.showerror("Chyba", f"Stratégia '{name}' neexistuje")
            return
        
        try:
            strategy = self.saved_strategies[name]
            
            # Načítaj hodnoty do kalkulátora
            self.symbol_var.set(strategy.get('symbol', 'SPY'))
            self.option_type_var.set(strategy.get('option_type', 'CALL'))
            self.calc_underlying_price_var.set(strategy.get('underlying_price', ''))
            self.calc_short_strike_var.set(strategy.get('short_strike', ''))
            self.calc_short_expiry_var.set(strategy.get('short_expiry', ''))
            self.calc_short_premium_var.set(strategy.get('short_premium', ''))
            self.calc_long_strike_var.set(strategy.get('long_strike', ''))
            self.calc_long_expiry_var.set(strategy.get('long_expiry', ''))
            self.calc_long_premium_var.set(strategy.get('long_premium', ''))
            self.broker_var.set(strategy.get('broker', 'IBKR'))
            
            # Aktualizuj last_used
            self.save_settings_file()
            
            if not auto:
                saved_at = strategy.get('saved_at', 'Neznámy dátum')
                self.update_calc_status(f"✓ Načítaná stratégia '{name}'")
                messagebox.showinfo("Načítané", f"Stratégia '{name}' bola načítaná.\n\nUložená: {saved_at}")
            else:
                self.update_calc_status(f"✓ Auto-načítaná '{name}'")
                
        except Exception as e:
            messagebox.showerror("Chyba", f"Nepodarilo sa načítať stratégiu:\n{e}")
    
    def delete_strategy(self):
        """Vyčistí polia kalkulátora (nemaze z archívu!)"""
        # Vyčisti polia
        self.strategy_name_var.set('')
        self.calc_underlying_price_var.set('')
        self.calc_short_strike_var.set('')
        self.calc_short_expiry_var.set('')
        self.calc_short_premium_var.set('')
        self.calc_long_strike_var.set('')
        self.calc_long_expiry_var.set('')
        self.calc_long_premium_var.set('')
        
        # Vyčisti výsledky
        if hasattr(self, 'calc_results_text'):
            self.calc_results_text.config(state='normal')
            self.calc_results_text.delete(1.0, tk.END)
            self.calc_results_text.config(state='disabled')
        
        self.update_calc_status("Kalkulátor vyčistený")


def main():
    root = tk.Tk()
    app = HedgeManagerGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
