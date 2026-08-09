"""Searchable, grouped audio device picker dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from grate.audio.capture import InputDeviceInfo

FAMILY_ORDER = ("dante", "voicemeeter", "physical", "virtual")
FAMILY_TITLES = {
    "dante": "Dante (DVS)",
    "voicemeeter": "Voicemeeter",
    "physical": "Physical",
    "virtual": "Other virtual",
}


class DevicePickerDialog(QDialog):
    def __init__(
        self,
        devices: list[InputDeviceInfo],
        current_name: str = "",
        *,
        title: str = "Select input device",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 480)
        self._devices = devices
        self._selected: InputDeviceInfo | None = None
        self._build(current_name)

    def _build(self, current_name: str) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search devices (e.g. dvs, voicemeeter, scarlett)…")
        self.search.textChanged.connect(self._repopulate)
        root.addWidget(self.search)

        opts = QHBoxLayout()
        self.wasapi_only = QCheckBox("WASAPI only (recommended)")
        self.wasapi_only.setChecked(True)
        self.wasapi_only.stateChanged.connect(self._repopulate)
        opts.addWidget(self.wasapi_only)
        opts.addStretch(1)
        root.addLayout(opts)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Device", "Host API", "Ch", "SR"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 40)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._accept_item)
        self.tree.itemSelectionChanged.connect(self._on_select)
        root.addWidget(self.tree, stretch=1)

        self.hint = QLabel("Tip: pick WASAPI devices when possible — cleaner for DVS / Voicemeeter.")
        self.hint.setStyleSheet("color:#6d7588; font-size:11px;")
        root.addWidget(self.hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._current_name = current_name
        self._repopulate()

    def _repopulate(self) -> None:
        query = self.search.text().strip().lower()
        wasapi_only = self.wasapi_only.isChecked()
        self.tree.clear()

        grouped: dict[str, list[InputDeviceInfo]] = {k: [] for k in FAMILY_ORDER}
        for d in self._devices:
            if wasapi_only and "WASAPI" not in d.hostapi_name:
                continue
            if query and query not in d.name.lower() and query not in d.hostapi_name.lower():
                continue
            grouped.setdefault(d.family, []).append(d)

        select_item: QTreeWidgetItem | None = None
        for fam in FAMILY_ORDER:
            items = grouped.get(fam) or []
            if not items:
                continue
            parent = QTreeWidgetItem([FAMILY_TITLES.get(fam, fam), "", "", ""])
            parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
            bold = parent.font(0)
            bold.setBold(True)
            parent.setFont(0, bold)
            self.tree.addTopLevelItem(parent)
            for d in items:
                child = QTreeWidgetItem(
                    [
                        d.name,
                        d.hostapi_name.replace("Windows ", ""),
                        str(d.channels),
                        str(int(d.sample_rate)),
                    ]
                )
                child.setData(0, Qt.UserRole, d)
                parent.addChild(child)
                if self._current_name and (
                    d.name == self._current_name or self._current_name.lower() in d.name.lower()
                ):
                    select_item = child
            parent.setExpanded(True)

        if select_item is not None:
            self.tree.setCurrentItem(select_item)
            self._selected = select_item.data(0, Qt.UserRole)

        if self.tree.topLevelItemCount() == 0:
            empty = QTreeWidgetItem(["No devices match", "", "", ""])
            empty.setFlags(empty.flags() & ~Qt.ItemIsSelectable)
            self.tree.addTopLevelItem(empty)

    def _on_select(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.UserRole)
        if isinstance(data, InputDeviceInfo):
            self._selected = data

    def _accept_item(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole)
        if isinstance(data, InputDeviceInfo):
            self._selected = data
            self.accept()

    def selected_device(self) -> InputDeviceInfo | None:
        return self._selected
