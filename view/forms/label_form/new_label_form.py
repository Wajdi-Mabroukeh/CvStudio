from PyQt5.QtWidgets import QDialog,QColorDialog

from util import GUIUtilities
from view.forms.label_form.base_new_label_form import Ui_NewLabelDialog
from vo import LabelVo


class NewLabelForm(QDialog, Ui_NewLabelDialog):
    def __init__(self,parent=None):
        super(NewLabelForm, self).__init__(parent)
        self.setupUi(self)
        self.setWindowTitle("Create new label".title())
        self.setWindowIcon(GUIUtilities.get_icon("python.png"))
        self.btn_pick_color.clicked.connect(self.btn_pick_color_click_slot)
        self._result=None

    @property
    def result(self)-> LabelVo:
        vo = LabelVo()
        vo.name = self.nameLineEdit.text()
        vo.color = self.colorLineEdit.text()
        return vo

    def btn_pick_color_click_slot(self):
        color=QColorDialog.getColor()
        if color.isValid():
            self.colorLineEdit.setText(color.name())
