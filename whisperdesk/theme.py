ACCENTS = [
    ('Червоний', '#C94732'), ('Помаранчевий', '#DF7627'),
    ('Жовтий', '#E7B52B'), ('Зелений', '#39844C'),
    ('Бірюзовий', '#087F83'), ('Синій', '#226DAA'),
    ('Фіолетовий', '#854CA4'), ('Малиновий', '#B62C60'),
]


def stylesheet(night=False, accent=0):
    a = ACCENTS[accent][1]
    bg, panel, field, border, text, muted = (
        ('#241A16', '#30231D', '#382A23', '#634C3B', '#F2E4CF', '#C0A98E') if night else
        ('#EFE3CE', '#F6ECDC', '#FFF8ED', '#CDB99D', '#3B2A20', '#79634E'))
    button_text = '#2A1B12' if accent in (1, 2) else '#FFFFFF'
    return f'''
    QWidget {{ background: {bg}; color: {text}; font-family: "Segoe UI"; font-size: 13px; }}
    QFrame#panel, QGroupBox {{ background: {panel}; border: 1px solid {border}; border-radius: 9px; }}
    QGroupBox {{ margin-top: 13px; padding: 14px 10px 9px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; }}
    QLabel {{ background: transparent; }}
    QLabel#brand {{ font-size: 23px; font-weight: 700; }}
    QLabel#heading {{ font-size: 17px; font-weight: 600; }}
    QLabel#muted {{ color: {muted}; }}
    QPushButton {{ background: {panel}; border: 1px solid {border}; border-bottom: 2px solid {border};
                   border-radius: 6px; padding: 8px 12px; }}
    QPushButton:hover {{ border: 1px solid {a}; border-bottom: 2px solid {a}; }}
    QPushButton:pressed {{ background: {border}; }}
    QPushButton:disabled {{ color: {muted}; background: {bg}; }}
    QPushButton#primary {{ background: {a}; border-color: {a}; color: {button_text}; font-weight: 600; }}
    QPushButton#primary:disabled {{ background: {border}; color: {muted}; }}
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{ background: {field}; border: 1px solid {border};
                    border-radius: 5px; padding: 7px; selection-background-color: {a}; }}
    QPlainTextEdit {{ font-family: "Consolas"; font-size: 14px; padding: 12px; }}
    QComboBox QAbstractItemView {{ background: {field}; selection-background-color: {a}; }}
    QListWidget {{ background: {panel}; border: none; outline: none; }}
    QListWidget::item {{ padding: 15px 8px; margin: 3px; border-radius: 6px; border: 1px solid transparent; }}
    QListWidget::item:selected {{ background: {field}; color: {text}; border: 2px solid {a}; }}
    QProgressBar {{ background: {border}; border: none; border-radius: 5px; text-align: center; min-height: 17px; }}
    QProgressBar::chunk {{ background: {a}; border-radius: 5px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {border}; border-radius: 3px; background: {field}; }}
    QCheckBox::indicator:checked {{ background: {a}; border: 1px solid {a}; }}
    QScrollBar:vertical {{ background: {bg}; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {border}; min-height: 25px; border-radius: 4px; }}
    QToolTip {{ color: {text}; background: {field}; border: 1px solid {border}; }}
    '''
