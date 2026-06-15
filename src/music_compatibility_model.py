from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex
from PySide6.QtGui import QColor

class MusicCompatibilityTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._headers = [
            "",
            "File",
            "Status",
            "Reason",
            "EQ Compatibility",
            "Codec",
            "Sample Rate (Hz)",
            "Bit Depth",
            "Channels",
            "Extension",
            "Block Size",
            "DSD",
            "Streams",
            "File Name Compatibility",
        ]
        self._rows = []
        self._checked_files = set()
        
        self._mapping = {
            1: 0,   # File
            2: 3,   # Status
            3: 4,   # Reason
            4: 9,   # EQ Compatibility
            5: 2,   # Codec
            6: 5,   # Sample Rate
            7: 6,   # Bit Depth
            8: 10,  # Channels
            9: 1,   # Extension
            10: 7,  # Block Size
            11: 8,  # DSD
            12: 11, # Streams
            13: 12, # File Name Compatibility
        }

    def update_data(self, rows):
        self.beginResetModel()
        self._rows = rows
        self._checked_files.clear()
        for row in self._rows:
            if row[14] == "unsupported":  # category is index 14
                self._checked_files.add(row[0])  # file path is index 0
        self.endResetModel()
        
    def get_checked_files(self):
        return self._checked_files

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        row_data = self._rows[row]

        if role == Qt.DisplayRole:
            if col == 0:
                return ""
            if col in self._mapping:
                return str(row_data[self._mapping[col]])

        elif role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if row_data[0] in self._checked_files else Qt.Unchecked

        elif role == Qt.ToolTipRole:
            if col == 3:
                return row_data[4] # reason
            elif col == 13:
                return row_data[13] # filename_compatibility_reason
            elif col != 0:
                if col in self._mapping:
                    return str(row_data[self._mapping[col]])

        elif role == Qt.BackgroundRole:
            status = row_data[3]
            eq_comp = row_data[9]
            filename_comp = row_data[12]
            
            if col == 2:
                if status == "SUPPORTED": return QColor("#2E7D32")
                elif status == "SKIPPED": return QColor("#3C3C3C")
                elif status in ("UNSUPPORTED", "UNKNOWN"): return QColor("#7A2C2C")
            elif col == 4:
                if eq_comp == "Not EQ Compatible": return QColor("#7A2C2C")
                elif eq_comp == "EQ Compatible": return QColor("#2E7D32")
                elif eq_comp == "UNKNOWN": return QColor("#7A5E2C")
            elif col in (6, 7):
                try:
                    sr = int(row_data[5])
                    if sr > 192000: return QColor("#7A2C2C")
                except: pass
                try:
                    bd = int(row_data[6])
                    if bd > 16: return QColor("#7A2C2C")
                except: pass
            elif col == 10:
                try:
                    bs = int(row_data[7])
                    if bs > 4096: return QColor("#7A5E2C")
                except: pass
            elif col == 13:
                if filename_comp == "INCOMPATIBLE": return QColor("#7A5E2C")
                elif filename_comp == "COMPATIBLE": return QColor("#2E7D32")

        elif role == Qt.TextAlignmentRole:
            if col in (6, 7, 8, 10, 11, 12):
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self._headers[section]
            elif role == Qt.ToolTipRole:
                if section == 4:
                    return "Indicates equaliser compatibility only. The file can still play, but if marked not compatible the equaliser will be disabled."
                elif section == 8:
                    return "Audio channel count from the selected audio stream. Multi-channel FLACs shown here."
                elif section == 12:
                    return "Total number of streams in the container (audio, video, artwork, etc.)."
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        if role == Qt.CheckStateRole and index.column() == 0:
            file_path = self._rows[index.row()][0]
            if value == Qt.Checked:
                self._checked_files.add(file_path)
            else:
                self._checked_files.discard(file_path)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemIsUserCheckable
        return flags
