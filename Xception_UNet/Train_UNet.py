import os
import sys
import numpy
import random
import pandas
import argparse
import girder_client
import tensorflow
import tensorflow.keras
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.callbacks import EarlyStopping,ModelCheckpoint,LearningRateScheduler,ReduceLROnPlateau
from tensorflow.keras import layers
from tensorflow.keras.models import model_from_json
import sklearn
import sklearn.metrics
import cv2
from matplotlib import pyplot as plt
import UNet
from unetSequence import unetSequence

from tensorflow.keras import backend as K

gpus = tensorflow.config.experimental.list_physical_devices('GPU')
tensorflow.config.experimental.set_memory_growth(gpus[0], True)

FLAGS = None

class Train_UNet:

    #Loads the data from the specified CSV file
    # fold: The fold number given in the CSV file (should be an int)
    # set: which set the images and labels make up (should be one of: "Train","Validation", or "Test")
    # Returns:
    #   images: The list of images loaded from the files or girderIDs
    #   imageLabels: a dictionary of the labels for each image, indexed by the label name
    def loadData(self, fold, set, dataset):
        entries = dataset.loc[(dataset["Fold"] == fold) & (dataset["Set"] == set)]
        return entries.index

    def process_ultrasound(self, image):
        resized = cv2.resize(image, (128, 128)).astype(numpy.float16)
        scaled = resized / resized.max()
        return scaled[...,numpy.newaxis]

    def process_seg(self, image):
        resized = cv2.resize(image, (128, 128))
        return resized[...,numpy.newaxis] / 255

    def convertTextToNumericLabels(self,textLabels,labelValues):
        numericLabels =[]
        for i in range(len(textLabels)):
            label = numpy.zeros(len(labelValues))
            labelIndex = numpy.where(labelValues == textLabels[i])
            label[labelIndex] = 1
            numericLabels.append(label)
        return numpy.array(numericLabels)

    def saveTrainingInfo(self,foldNum,saveLocation,trainingHistory,results):
        LinesToWrite = []
        folds = "Fold " + str(foldNum) +"/"+ str(self.numFolds)
        modelType = "\nNetwork type: " + str(self.networkType)
        LinesToWrite.append(modelType)
        datacsv = "\nData CSV: " + str(FLAGS.data_csv_file)
        LinesToWrite.append(datacsv)
        numEpochs = "\nNumber of Epochs: " + str(len(trainingHistory["loss"]))
        LinesToWrite.append(numEpochs)
        batch_size = "\nBatch size: " + str(self.batch_size)
        LinesToWrite.append(batch_size)
        LearningRate = "\nLearning rate: " + str(self.learning_rate)
        LinesToWrite.append(LearningRate)
        LossFunction = "\nLoss function: " + str(self.loss_Function)
        LinesToWrite.append(LossFunction)
        trainStatsHeader = "\n\nTraining Statistics: "
        LinesToWrite.append(trainStatsHeader)
        trainLoss = "\n\tFinal training loss: " + str(trainingHistory["loss"][-1])
        LinesToWrite.append(trainLoss)
        for i in range(len(self.metrics)):
            trainMetrics = "\n\tFinal training " + self.metrics[i] + ": " + str(trainingHistory[self.metrics[i]][-1])
            LinesToWrite.append(trainMetrics)
        trainLoss = "\n\tFinal validation loss: " + str(trainingHistory["val_loss"][-1])
        LinesToWrite.append(trainLoss)
        for i in range(len(self.metrics)):
            valMetrics = "\n\tFinal validation " + self.metrics[i] + ": " + str(trainingHistory["val_"+self.metrics[i]][-1])
            LinesToWrite.append(valMetrics)
        testStatsHeader = "\n\nTesting Statistics: "
        LinesToWrite.append(testStatsHeader)
        testLoss = "\n\tTest loss: " + str(results[0])
        LinesToWrite.append(testLoss)
        for i in range(len(self.metrics)):
            testMetrics = "\n\tTest " + self.metrics[i] + ": " + str(results[i+1])
            LinesToWrite.append(testMetrics)

        with open(os.path.join(saveLocation,"trainingInfo.txt"),'w') as f:
            f.writelines(LinesToWrite)

    def saveTrainingPlot(self,saveLocation,history,metric):
        fig = plt.figure()
        plt.plot([x for x in range(len(history[metric]))], history[metric], 'bo', label='Training '+metric)
        plt.plot([x for x in range(len(history["val_"+metric]))], history["val_" + metric], 'b', label='Validation '+metric)
        plt.title('Training and Validation ' + metric)
        plt.xlabel('Epochs')
        plt.ylabel(metric)
        plt.legend()
        plt.savefig(os.path.join(saveLocation, metric + '.png'))

    def train(self):
        self.saveLocation = FLAGS.save_location
        self.networkType = os.path.basename(os.path.dirname(self.saveLocation))
        self.dataCSVFile = pandas.read_csv(FLAGS.data_csv_file)
        self.numEpochs = FLAGS.num_epochs
        self.batch_size = FLAGS.batch_size
        self.learning_rate = FLAGS.learning_rate
        self.optimizer = tensorflow.keras.optimizers.Adam(learning_rate=self.learning_rate)
        self.loss_Function = multiclass_weighted_cross_entropy([0.1,0.9])
        self.metrics = ['IoU','accuracy']
        self.numFolds = self.dataCSVFile["Fold"].max() + 1
        self.gClient = None
        network = UNet.UNet()
        for fold in range(0,self.numFolds):
            foldDir = self.saveLocation+"_Fold_"+str(fold)
            if not os.path.exists(foldDir):
                os.mkdir(foldDir)
            labelName = "Segmentation_Left_Calyx_LG-segmentation" #This should be the label that will be used to train the network

            trainIndexes = self.loadData(fold, "Train", self.dataCSVFile)
            valIndexes = self.loadData(fold, "Validation", self.dataCSVFile)
            testIndexes = self.loadData(fold, "Test", self.dataCSVFile)

            trainDataSet = unetSequence(self.dataCSVFile, trainIndexes, self.batch_size, labelName,self.gClient, None,shuffle=True)
            valDataSet = unetSequence(self.dataCSVFile, valIndexes, self.batch_size, labelName, self.gClient,None,shuffle=False)
            testDataSet = unetSequence(self.dataCSVFile, testIndexes, self.batch_size, labelName, self.gClient,None,shuffle=False)

            K.clear_session()
            model = network.createModel((128,128,3),num_classes=2)

            print(model.summary())
            model.compile(optimizer = self.optimizer, loss = self.loss_Function, metrics = [IoU, 'accuracy'])
            earlyStoppingCallback = EarlyStopping(monitor='val_IoU', mode='max', verbose=1, patience=10)
            modelCheckPointCallback = ModelCheckpoint(os.path.join(foldDir, 'resnet50.h5'), verbose=1,
                                                      monitor='val_IoU', mode='max', save_weights_only=True,
                                                      save_best_only=True)
            # learningRateSchedule = self.stepDecaySchedule(initialLR=self.cnn_learning_rate,decay=0.7,stepSize=3)
            learningRateSchedule = ReduceLROnPlateau(monitor='val_loss', mode='min', verbose=1, factor=0.7,
                                                     patience=4, epsilon=0.001, cooldown=0)
            history = model.fit(x=trainDataSet,
                                validation_data=valDataSet,
                                epochs = self.numEpochs,
                                callbacks=[earlyStoppingCallback,modelCheckPointCallback,learningRateSchedule])

            results = model.evaluate(x = testDataSet,
                                     batch_size = self.batch_size)
            network.saveModel(model,foldDir)
            self.saveTrainingInfo(fold,foldDir,history.history,results)
            self.saveTrainingPlot(foldDir,history.history,"loss")
            for metric in self.metrics:
                self.saveTrainingPlot(foldDir,history.history,metric)

def IoU_loss(y_true,y_pred):
    smooth = 1e-12
    intersection = K.sum(y_true[:,:,:,1] * y_pred[:,:,:,1])        #Create intersection
    sum_ = K.sum(y_true[:,:,:,1] + y_pred[:,:,:,1])                #Create union
    jac = (intersection + smooth) / (sum_ - intersection + smooth) #Divide and smooth
    return K.mean(1-jac) #Return 1-IoU so it can be use as a measurement of loss

def mean_IoU_loss(numClasses,class_weights=[0.5,0.5]):

    if not isinstance(class_weights, tensorflow.Tensor):
        class_weights = tensorflow.constant(class_weights)
    meanIOU = tensorflow.keras.metrics.MeanIoU(numClasses)

    def loss(y_true, y_pred):
        iou_value = meanIOU.update_state(y_true,y_pred,class_weights)
        return 1-iou_value

    return loss

def multiclass_focal_loss(class_weights,gamma):
    """
    Focal loss.
        FL(p, p̂) = -∑class_weights*(1-p̂)ᵞ*p*log(p̂)
    Used as loss function for multi-class image segmentation with one-hot encoded masks.
    :param class_weights: Class weight coefficients (Union[list, np.ndarray, tf.Tensor], len=<N_CLASSES>)
    :param gamma: Focusing parameters, γ_i ≥ 0 (Union[list, np.ndarray, tf.Tensor], len=<N_CLASSES>)
    :return: Focal loss function (Callable[[tf.Tensor, tf.Tensor], tf.Tensor])
    """
    if not isinstance(class_weights, tensorflow.Tensor):
        class_weights = tensorflow.constant(class_weights)
    if not isinstance(gamma, tensorflow.Tensor):
        gamma = tensorflow.constant(gamma)

    def loss(y_true, y_pred):
        """
        Compute focal loss.
        :param y_true: True masks (tf.Tensor, shape=(<BATCH_SIZE>, <IMAGE_HEIGHT>, <IMAGE_WIDTH>, <N_CLASSES>))
        :param y_pred: Predicted masks (tf.Tensor, shape=(<BATCH_SIZE>, <IMAGE_HEIGHT>, <IMAGE_WIDTH>, <N_CLASSES>))
        :return: Focal loss (tf.Tensor, shape=(None,))
        """
        f_loss = -(class_weights * (1-y_pred)**gamma * y_true * K.log(y_pred))

        # Average over each data point/image in batch
        axis_to_reduce = range(1, K.ndim(f_loss))
        f_loss = K.mean(f_loss, axis=axis_to_reduce)

        return f_loss

    return loss

def IoU(y_true,y_pred):
    smooth = 1e-12
    y_pred_pos = K.round(K.clip(y_pred[:,:,:,1], 0, 1))             #Extract binary mask from probability map
    intersection = K.sum(y_true[:,:,:,1] * y_pred_pos)              #Create union
    sum_ = K.sum(y_true[:,:,:,1] + y_pred[:,:,:,1])                 #Create intersection
    jac = (intersection + smooth) / (sum_ - intersection + smooth)  #Divide and smooth
    return K.mean(jac) #Return the mean jaccard index as IoU

def multiclass_weighted_cross_entropy(class_weights, is_logits = False):
    """
    Multi-class weighted cross entropy.
        WCE(p, p̂) = −Σp*log(p̂)*class_weights
    Used as loss function for multi-class image segmentation with one-hot encoded masks.
    :param class_weights: Weight coefficients (list of floats)
    :param is_logits: If y_pred are logits (bool)
    :return: Weighted cross entropy loss function (Callable[[tf.Tensor, tf.Tensor], tf.Tensor])
    """
    if not isinstance(class_weights, tensorflow.Tensor):
        class_weights = tensorflow.constant(class_weights)

    def loss(y_true, y_pred):
        """
        Computes the weighted cross entropy.
        :param y_true: Ground truth (tf.Tensor, shape=(None, None, None, None))
        :param y_pred: Predictions (tf.Tensor, shape=(<BATCH_SIZE>, <IMAGE_HEIGHT>, <IMAGE_WIDTH>, <N_CLASSES>))
        :return: Weighted cross entropy (tf.Tensor, shape=(<BATCH_SIZE>,))
        """
        assert len(class_weights) == y_pred.shape[-1], f"Number of class_weights ({len(class_weights)}) needs to be the same as number " \
                                                 f"of classes ({y_pred.shape[-1]})"

        if is_logits:
            y_pred = tensorflow.keras.activations.softmax(y_pred, axis=-1)

        y_pred = K.clip(y_pred, K.epsilon(), 1-K.epsilon())  # To avoid unwanted behaviour in K.log(y_pred)

        # p * log(p̂) * class_weights
        wce_loss = y_true * K.log(y_pred) * class_weights

        # Average over each data point/image in batch
        axis_to_reduce = range(1, K.ndim(wce_loss))
        wce_loss = K.mean(wce_loss, axis=axis_to_reduce)

        return -wce_loss

    return loss

if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument(
      '--save_location',
      type=str,
      default='C:/repos/aigt/DeepLearnLive/Networks/Vessel_UNet/EMBC/FullDataset1',
      help='Name of the directory where the models and results will be saved'
  )
  parser.add_argument(
      '--data_csv_file',
      type=str,
      default='C:/repos/aigt/DeepLearnLive/Datasets/US_Vessel_Segmentations/FullDataset_UNET.csv',
      help='Path to the csv file containing locations for all data used in training'
  )
  parser.add_argument(
      '--num_epochs',
      type=int,
      default=500,
      help='number of epochs used in training'
  )
  parser.add_argument(
      '--batch_size',
      type=int,
      default=8,
      help='type of output your model generates'
  )
  parser.add_argument(
      '--learning_rate',
      type=float,
      default=1e-6,
      help='Learning rate used in training'
  )
  parser.add_argument(
      '--loss_function',
      type=str,
      default='categorical_crossentropy',
      help='Name of the loss function to be used in training (see keras documentation).'
  )
  parser.add_argument(
      '--metrics',
      type=str,
      default='accuracy',
      help='Metrics used to evaluate model.'
  )
FLAGS, unparsed = parser.parse_known_args()
tm = Train_UNet()
tm.train()
