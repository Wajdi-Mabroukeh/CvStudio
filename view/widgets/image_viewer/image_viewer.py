import math
import os
from collections import OrderedDict

import cv2
import imutils
import numpy as np

from PIL import Image
from PyQt5 import QtCore,QtGui
from PyQt5.QtCore import QSize,QThreadPool,QPointF,QPoint,QRectF,QItemSelection,QModelIndex
from PyQt5.QtGui import QPixmap,QCursor
from PyQt5.QtWidgets import QWidget,QGraphicsItem,QAbstractItemView,QDialog,QAction, \
    QLabel,QGraphicsScene,QMenu,QGraphicsDropShadowEffect

from core import HubClientFactory,Framework
from dao import DatasetDao,AnnotaDao
from dao.hub_dao import HubDao
from dao.label_dao import LabelDao
from decor import gui_exception,work_exception
from util import GUIUtilities,Worker
from view.forms import NewRepoForm
from view.forms.label_form import NewLabelForm
from view.widgets.common.custom_list import CustomListWidgetItem
from view.widgets.image_viewer import ImageViewer
from view.widgets.image_viewer.selection_mode import SELECTION_MODE
from view.widgets.labels_tableview import LabelsTableView
from view.widgets.loading_dialog import QLoadingDialog
from view.widgets.models_treeview import ModelsTreeview
from vo import LabelVO,DatasetEntryVO,AnnotaVO
from .base_image_viewer import Ui_Image_Viewer_Widget
from .items import EditableBox,EditablePolygon,EditableItem,EditableEllipse
from ..image_button import ImageButton
import more_itertools


class ImageViewerWidget(QWidget,Ui_Image_Viewer_Widget):
    def __init__(self,parent=None):
        super(ImageViewerWidget,self).__init__(parent)
        self.setupUi(self)
        self.viewer=ImageViewer()
        self.viewer.scene().itemAdded.connect(self._scene_item_added)
        self.viewer.extreme_points_selection_done_sgn.connect(self.extreme_points_selection_done_slot)
        self.center_layout.addWidget(self.viewer,0,0)

        # self._label_background=QLabel()
        # self._label_background.setFixedHeight(40)
        # image = GUIUtilities.get_image("label.png")
        # self._label_background.setPixmap(image.scaledToHeight(40))
        # self.center_layout.addWidget(self._label_background,0,0,QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

        self._label=QLabel()
        self._label.setVisible(False)
        self._label.setMargin(5)
        self._label.setStyleSheet('''
            QLabel{
            font: 12pt;
            border-radius: 25px;
            margin: 10px;
            color: black; 
            background-color: #FFFFDC;
            }
        ''')
        shadow=QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        # shadow.setColor(QtGui.QColor(76,35,45).lighter())
        shadow.setColor(QtGui.QColor(94, 93, 90).lighter())
        shadow.setOffset(2)
        self._label.setGraphicsEffect(shadow)
        self.center_layout.addWidget(self._label,0,0,QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

        self.actions_layout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        self.actions_layout.setContentsMargins(0,5,0,0)
        self._ds_dao =  DatasetDao()
        self._hub_dao = HubDao()
        self._labels_dao = LabelDao()
        self._annot_dao = AnnotaDao()
        self._thread_pool=QThreadPool()
        self._loading_dialog=QLoadingDialog()
        self._source = None
        self._image = None
        self.images_list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection )
        #self.images_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.images_list_widget.currentItemChanged.connect(self.image_list_sel_changed_slot)
        self.images_list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.images_list_widget.customContextMenuRequested.connect(self.image_list_context_menu)

        self.treeview_models=ModelsTreeview()
        self.treeview_models.setColumnWidth(0,300)
        self.tree_view_models_layout.addWidget(self.treeview_models)
        self.treeview_models.action_click.connect(self.trv_models_action_click_slot)

        self.treeview_labels = LabelsTableView()
        self.treeview_labels.action_click.connect(self.trv_labels_action_click_slot)
        self.tree_view_labels_layout.addWidget(self.treeview_labels)
        self.treeview_labels.selectionModel().selectionChanged.connect(self.default_label_changed_slot)
        #window = GUIUtilities.findMainWindow()
        #window.keyPressed.connect(self.window_keyPressEvent)
        self.create_actions_bar()

    def image_list_context_menu(self, pos: QPoint):
        menu=QMenu()
        result=self._labels_dao.fetch_all(self.source.dataset)
        if len(result) > 0:
            labels_menu=menu.addMenu("labels")
            for vo in result:
                action=labels_menu.addAction(vo.name)
                action.setData(vo)
        action=menu.exec_(QCursor.pos())
        if action and isinstance(action.data(),LabelVO):
            label=action.data()
            self.change_image_labels(label)

    def change_image_labels(self, label: LabelVO):
        items = self.images_list_widget.selectedItems()
        selected_images = []
        for item in items:
            vo = item.tag
            selected_images.append(vo)

        @work_exception
        def do_work():
            self._ds_dao.tag_entries(selected_images, label)
            return 1,None

        @gui_exception
        def done_work(result):
            status, err = result
            if err:
                raise err

        worker = Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)


    def default_label_changed_slot(self, selection: QItemSelection):
        selected_rows =self.treeview_labels.selectionModel().selectedRows(2)
        if len(selected_rows) > 0:
            index: QModelIndex = selected_rows[0]
            current_label: LabelVO=self.treeview_labels.model().data(index)
            self.viewer.current_label = current_label

    def image_list_sel_changed_slot(self,curr: CustomListWidgetItem,prev: CustomListWidgetItem):
        if curr:
            self.source = curr.tag
            self.load_image()

    @property
    def image(self):
        return self._image

    @property
    def source(self)-> DatasetEntryVO:
        return self._source

    @source.setter
    def source(self, value):
        if not isinstance(value, DatasetEntryVO):
            raise Exception("Invalid source")
        self._source= value
        image_path = self._source.file_path
        self._image=Image.open(image_path)
        self.viewer.pixmap=QPixmap(image_path)


    @gui_exception
    def load_images(self):
        @work_exception
        def do_work():
            entries=self._ds_dao.fetch_entries(self.source.dataset)
            return entries,None

        @gui_exception
        def done_work(result):
            data,error=result
            selected_item = None
            for vo in data:
                item = CustomListWidgetItem(vo.file_path)
                item.setIcon(GUIUtilities.get_icon("image.png"))
                item.tag = vo
                if vo.file_path == self.source.file_path:
                    selected_item = item
                self.images_list_widget.addItem(item)
                self.images_list_widget.setCursor(QtCore.Qt.PointingHandCursor)
                self.images_list_widget.setCurrentItem(selected_item)

        worker=Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)


    @gui_exception
    def load_models(self):
        @work_exception
        def do_work():
            results = self._hub_dao.fetch_all()
            return results, None

        @gui_exception
        def done_work(result):
            result, error = result
            if result:
                for model in result:
                    self.treeview_models.add_node(model)
        worker=Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)

    @gui_exception
    def load_labels(self):
        @work_exception
        def do_work():
            results = self._labels_dao.fetch_all(self.source.dataset)
            return results, None

        @gui_exception
        def done_work(result):
            result, error = result
            if error is None:
                for entry in result:
                    self.treeview_labels.add_row(entry)
        worker=Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)

    @gui_exception
    def load_image_annotations(self):
        @work_exception
        def do_work():
            results=self._annot_dao.fetch_all(self.source.id)
            return results,None

        @gui_exception
        def done_work(result):
            result,error=result
            if error:
                raise error
            img_bbox: QRectF=self.viewer.pixmap.sceneBoundingRect()
            offset=QPointF(img_bbox.width()/2,img_bbox.height()/2)
            for entry in result:
                try:
                    vo: AnnotaVO=entry
                    points=map(float,vo.points.split(","))
                    points=list(more_itertools.chunked(points,2))
                    if vo.kind == "box" or vo.kind == "ellipse":
                        x=points[0][0]-offset.x()
                        y=points[0][1]-offset.y()
                        w=math.fabs(points[0][0]-points[1][0])
                        h=math.fabs(points[0][1]-points[1][1])
                        roi: QRectF=QRectF(x,y,w,h)
                        if vo.kind == "box":
                            item=EditableBox(roi)
                        else:
                            item=EditableEllipse()
                        item.setRect(roi)
                        item.label=vo.label
                        self.viewer.scene().addItem(item)
                    elif vo.kind == "polygon":
                        item=EditablePolygon()
                        item.label=vo.label
                        self.viewer.scene().addItem(item)
                        for p in points:
                            item.addPoint(QPoint(p[0]-offset.x(),p[1]-offset.y()))
                except Exception as ex:
                    GUIUtilities.show_error_message("Error loading the annotations: {}".format(ex), "Error")
        worker=Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)

    @gui_exception
    def load_image_label(self):
        @work_exception
        def do_work():
            label=self._annot_dao.get_label(self.source.id)
            return label,None

        @gui_exception
        def done_work(result):
            label_name,error=result
            if error:
                raise error
            if label_name:
                self._label.setVisible(True)
                self._label.setText(label_name)
            else:
                self._label.setVisible(False)
                self._label.setText("")
        worker=Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)

    @gui_exception
    def add_repository(self):

        @work_exception
        def do_work(repo):
            hub_client=HubClientFactory.create(Framework.PyTorch)
            hub=hub_client.fetch_model(repo,force_reload=True)
            self._hub_dao.save(hub)
            return hub,None

        @gui_exception
        def done_work(result):
            self._loading_dialog.close()
            data,error=result
            if error is None:
                self.treeview_models.add_node(data)

        form=NewRepoForm()
        if form.exec_() == QDialog.Accepted:
            repository=form.result
            worker=Worker(do_work, repository)
            worker.signals.result.connect(done_work)
            self._thread_pool.start(worker)
            self._loading_dialog.exec_()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        try:
            self.viewer.vline.hide()
            self.viewer.hline.hide()
            row = self.images_list_widget.currentRow()
            last_index = self.images_list_widget.count() - 1
            if event.key() == QtCore.Qt.Key_A:
                self.save_annotations()
                if row > 0:
                    self.images_list_widget.setCurrentRow(row-1)
                else:
                    self.images_list_widget.setCurrentRow(last_index)
            if event.key() == QtCore.Qt.Key_D:
                self.save_annotations()
                if row < last_index:
                    self.images_list_widget.setCurrentRow(row+1)
                else:
                    self.images_list_widget.setCurrentRow(0)
            if event.key() == QtCore.Qt.Key_W:
                self.viewer.selection_mode=SELECTION_MODE.POLYGON
            if event.key() == QtCore.Qt.Key_S:
                self.viewer.selection_mode=SELECTION_MODE.BOX
            super(ImageViewerWidget,self).keyPressEvent(event)
        except Exception as ex:
            GUIUtilities.show_error_message(str(ex), "Error")

    @gui_exception
    def trv_models_action_click_slot(self, action:  QAction):
        if action.text() == self.treeview_models.CTX_MENU_NEW_DATASET_ACTION:
            self.add_repository()
        elif action.text() == self.treeview_models.CTX_MENU_AUTO_LABEL_ACTION:
            current_node = action.data()  # model name
            parent_node = current_node.parent  # repo
            repo, model = parent_node.get_data(0),current_node.get_data(0)
            self.autolabel(repo, model)

    @gui_exception
    def trv_labels_action_click_slot(self,action: QAction):
        model  = self.treeview_labels.model()
        if action.text() == self.treeview_labels.CTX_MENU_ADD_LABEL:
            form=NewLabelForm()
            if form.exec_() == QDialog.Accepted:
                label_vo: LabelVO=form.result
                label_vo.dataset=self.source.dataset
                label_vo = self._labels_dao.save(label_vo)
                self.treeview_labels.add_row(label_vo)
        elif action.text() == self.treeview_labels.CTX_MENU_DELETE_LABEL:
            index : QModelIndex = action.data()
            if index:
                label_vo=model.index(index.row(),2).data()
                self._labels_dao.delete(label_vo.id)
                self.viewer.remove_annotations_by_label(label_vo.name)
                model.removeRow(index.row())

    def autolabel(self, repo, model_name):
        def do_work():
            try:
                from PIL import Image
                from torchvision import transforms
                import torch
                gpu_id=0
                device=torch.device("cuda:"+str(gpu_id) if torch.cuda.is_available() else "cpu")
                model=torch.hub.load(repo,model_name,pretrained=True)
                model.eval()
                input_image=Image.open(self.source.file_path)
                preprocess=transforms.Compose([
                    transforms.Resize(480),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=[0.485,0.456,0.406],std=[0.229,0.224,0.225]),
                ])
                input_tensor=preprocess(input_image)
                input_batch=input_tensor.unsqueeze(0)  # create a mini-batch as expected by the model
                # # move the param and model to GPU for speed if available
                if torch.cuda.is_available():
                    input_batch=input_batch.to(device)
                    model.to(device)
                with torch.no_grad():
                    output=model(input_batch)['out'][0]
                output_predictions=output.argmax(0)
                # create a color pallette, selecting a color for each class
                palette=torch.tensor([2 ** 25-1,2 ** 15-1,2 ** 21-1])
                colors=torch.as_tensor([i for i in range(21)])[:,None]*palette
                colors=(colors%255).numpy().astype("uint8")
                # plot the semantic segmentation predictions of 21 classes in each color
                predictions_array: np.ndarray=output_predictions.byte().cpu().numpy()
                predictions_image=Image.fromarray(predictions_array).resize(input_image.size)
                predictions_image.putpalette(colors)
                labels_mask=np.asarray(predictions_image)
                classes=list(filter(lambda x: x != 0,np.unique(labels_mask).tolist()))
                classes_map={c: [] for c in classes}
                for c in classes:
                    class_mask=np.zeros(labels_mask.shape,dtype=np.uint8)
                    class_mask[np.where(labels_mask == c)]=255
                    contour_list=cv2.findContours(class_mask.copy(),cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
                    contour_list=imutils.grab_contours(contour_list)
                    for contour in contour_list:
                        points=np.vstack(contour).squeeze().tolist()
                        classes_map[c].append(points)
                return classes_map,None
            except Exception as ex:
                return None,ex

        def done_work(result):
            try:
                self._loading_dialog.close()
                classes_map,err=result
                if err:
                    return
                for class_idx,contours in classes_map.items():
                    if len(contours) > 0:
                        for c in contours:
                            points=[]
                            for i in range(0,len(c),10):
                                points.append(c[i])
                            if len(points) > 5:
                                polygon=EditablePolygon()
                                self.viewer._scene.addItem(polygon)
                                bbox: QRectF=self.viewer.pixmap.boundingRect()
                                offset=QPointF(bbox.width()/2,bbox.height()/2)
                                for point in points:
                                    if isinstance(point, list) and len(point) == 2:
                                        polygon.addPoint(QPoint(point[0]-offset.x(),point[1]-offset.y()))
            except Exception as ex:
                print(ex)

        worker=Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)
        self._loading_dialog.exec_()

    def create_actions_bar(self):
        icon_size = QSize(28,28)

        self.btn_enable_polygon_selection=ImageButton(icon=GUIUtilities.get_icon("polygon.png"),size=icon_size)
        self.btn_enable_rectangle_selection=ImageButton(icon=GUIUtilities.get_icon("square.png"),size=icon_size)
        self.btn_enable_circle_selection = ImageButton(icon=GUIUtilities.get_icon("circle.png"), size=icon_size)
        self.btn_enable_free_selection=ImageButton(icon=GUIUtilities.get_icon("highlighter.png"),size=icon_size)
        self.btn_enable_none_selection=ImageButton(icon=GUIUtilities.get_icon("cursor.png"),size=icon_size)
        self.btn_save_annotations = ImageButton(icon=GUIUtilities.get_icon("save-icon.png"),size=icon_size)
        self.btn_clear_annotations=ImageButton(icon=GUIUtilities.get_icon("clean.png"),size=icon_size)
        self.btn_supervised_annotation_tools = ImageButton(icon=GUIUtilities.get_icon("robotic-hand.png"), size=icon_size)

        self.actions_layout.addWidget(self.btn_enable_rectangle_selection)
        self.actions_layout.addWidget(self.btn_enable_polygon_selection)
        self.actions_layout.addWidget(self.btn_enable_circle_selection)
        self.actions_layout.addWidget(self.btn_enable_free_selection)
        self.actions_layout.addWidget(self.btn_supervised_annotation_tools)
        self.actions_layout.addWidget(self.btn_enable_none_selection)
        self.actions_layout.addWidget(self.btn_clear_annotations)
        self.actions_layout.addWidget(self.btn_save_annotations)

        self.btn_save_annotations.clicked.connect(self.btn_save_annotations_clicked_slot)
        self.btn_enable_polygon_selection.clicked.connect(self.btn_enable_polygon_selection_clicked_slot)
        self.btn_enable_rectangle_selection.clicked.connect(self.btn_enable_rectangle_selection_clicked_slot)
        self.btn_enable_free_selection.clicked.connect(self.btn_enable_free_selection_clicked_slot)
        self.btn_enable_none_selection.clicked.connect(self.btn_enable_none_selection_clicked_slot)
        self.btn_enable_circle_selection.clicked.connect(self.btn_enable_circle_selection_clicked_slot)
        self.btn_clear_annotations.clicked.connect(self.btn_clear_annotations_clicked_slot)
        self.btn_supervised_annotation_tools.clicked.connect(self.btn_supervised_annotation_tools_clicked_clot)

    def btn_supervised_annotation_tools_clicked_clot(self):
        menu=QMenu()
        menu.setCursor(QtCore.Qt.PointingHandCursor)
        ex_points_action = QAction("Extreme Points")
        ex_points_action.setIcon(GUIUtilities.get_icon("points.png"))
        menu.addAction(ex_points_action)
        curr_action=menu.exec_(QCursor.pos())
        if curr_action == ex_points_action:
            self.viewer.selection_mode = SELECTION_MODE.EXTREME_POINTS


    def btn_clear_annotations_clicked_slot(self):
        self.viewer.remove_annotations()

    def btn_enable_polygon_selection_clicked_slot(self):
        self.viewer.selection_mode=SELECTION_MODE.POLYGON

    def btn_enable_rectangle_selection_clicked_slot(self):
        self.viewer.selection_mode=SELECTION_MODE.BOX

    def btn_enable_circle_selection_clicked_slot(self):
        self.viewer.selection_mode=SELECTION_MODE.ELLIPSE

    def btn_enable_none_selection_clicked_slot(self):
        self.viewer.selection_mode=SELECTION_MODE.NONE

    def btn_enable_free_selection_clicked_slot(self):
        self.viewer.selection_mode=SELECTION_MODE.FREE

    def save_annotations(self):
        scene: QGraphicsScene=self.viewer.scene()
        annotations=[]
        for item in scene.items():
            image_rect : QRectF=self.viewer.pixmap.sceneBoundingRect()
            image_offset = QPointF(image_rect .width()/2,image_rect .height()/2)
            if isinstance(item,EditableItem):
                a=AnnotaVO()
                a.label=item.label.id if item.label else None
                a.entry=self.source.id
                a.kind=item.shape_type
                a.points=item.coordinates(image_offset)
                annotations.append(a)
        self._annot_dao.save(self.source.id, annotations)

    @gui_exception
    def btn_save_annotations_clicked_slot(self,*args, **kwargs):
        self.save_annotations()
        GUIUtilities.show_info_message("Annotations saved successfully", "Information")

    def _scene_item_added(self, item: QGraphicsItem):
        item.tag = self.source

    def bind(self):
        self.load_images()
        self.load_models()
        self.load_labels()

    def load_image(self):
        self.load_image_annotations()
        self.load_image_label()

    @gui_exception
    def extreme_points_selection_done_slot(self, points):
        import torch
        from collections import OrderedDict
        from PIL import Image
        import numpy as np
        from torch.nn.functional import upsample
        from contrib.dextr import deeplab_resnet  as resnet
        from contrib.dextr import helpers
        modelName='dextr_pascal-sbd'
        pad=50
        thres=0.8
        gpu_id=0

        def do_work():
            device=torch.device("cuda:"+str(gpu_id) if torch.cuda.is_available() else "cpu")
            #  Create the network and load the weights
            net=resnet.resnet101(1,nInputChannels=4,classifier='psp')
            model_path = os.path.abspath("./models/{}.pth".format(modelName))
            state_dict_checkpoint=torch.load(model_path, map_location=lambda storage,loc: storage)

            if 'module.' in list(state_dict_checkpoint.keys())[0]:
                new_state_dict=OrderedDict()
                for k,v in state_dict_checkpoint.items():
                    name=k[7:]  # remove `module.` from multi-gpu training
                    new_state_dict[name]=v
            else:
                new_state_dict=state_dict_checkpoint
            net.load_state_dict(new_state_dict)
            net.eval()
            net.to(device)

            #  Read image and click the points
            image=np.array(Image.open(self.source.file_path))
            extreme_points_ori = np.asarray(points).astype(np.int)
            with torch.no_grad():
                #  Crop image to the bounding box from the extreme points and resize
                bbox=helpers.get_bbox(image,points=extreme_points_ori,pad=pad,zero_pad=True)
                crop_image=helpers.crop_from_bbox(image,bbox,zero_pad=True)
                resize_image=helpers.fixed_resize(crop_image,(512,512)).astype(np.float32)

                #  Generate extreme point heat map normalized to image values
                extreme_points=extreme_points_ori-[np.min(extreme_points_ori[:,0]),np.min(extreme_points_ori[:,1])]+[pad,pad]
                extreme_points=(512*extreme_points*[1/crop_image.shape[1],1/crop_image.shape[0]]).astype(np.int)
                extreme_heatmap=helpers.make_gt(resize_image,extreme_points,sigma=10)
                extreme_heatmap=helpers.cstm_normalize(extreme_heatmap,255)

                #  Concatenate inputs and convert to tensor
                input_dextr=np.concatenate((resize_image,extreme_heatmap[:,:,np.newaxis]),axis=2)
                inputs=torch.from_numpy(input_dextr.transpose((2,0,1))[np.newaxis,...])

                # Run a forward pass
                inputs=inputs.to(device)
                outputs=net.forward(inputs)
                outputs=upsample(outputs,size=(512,512),mode='bilinear',align_corners=True)
                outputs=outputs.to(torch.device('cpu'))

                pred=np.transpose(outputs.data.numpy()[0,...],(1,2,0))
                pred=1/(1+np.exp(-pred))
                pred=np.squeeze(pred)
                result=helpers.crop2fullmask(pred,bbox,im_size=image.shape[:2],zero_pad=True,relax=pad) > thres
                binary = np.zeros_like(result, dtype=np.uint8)
                binary[result]=255
                contour_list=cv2.findContours(binary.copy(),cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
                contour_list=imutils.grab_contours(contour_list)
                contours = []
                for contour in contour_list:
                    c_points=np.vstack(contour).squeeze().tolist()
                    contours.append(c_points)
            return contours

        def done_work(contours):
            try:
                self._loading_dialog.close()
                if contours:
                    self.viewer.clear_extreme_points()
                    for c in contours:
                        c_points=[]
                        for i in range(0,len(c),10):
                            c_points.append(c[i])
                        if len(c_points) > 5:
                            polygon=EditablePolygon()
                            self.viewer._scene.addItem(polygon)
                            bbox: QRectF=self.viewer.pixmap.boundingRect()
                            offset=QPointF(bbox.width()/2,bbox.height()/2)
                            for point in c_points:
                                polygon.addPoint(QPoint(point[0] - offset.x(),point[1] - offset.y()))
            except Exception as ex:
                print(ex)

        worker=Worker(do_work)
        worker.signals.result.connect(done_work)
        self._thread_pool.start(worker)
        self._loading_dialog.exec_()








