import logging
import os
from datetime import datetime

import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
import pandas as pd
import tensorflow as tf

import deepreg.model.loss.image as image_loss
import deepreg.model.loss.label as label_loss
from deepreg.dataset.load import get_data_loader
from deepreg.dataset.loader.interface import DataLoader


def build_dataset(
    dataset_config: dict,
    preprocess_config: dict,
    mode: str,
    training: bool,
    repeat: bool,
) -> [(DataLoader, None), (tf.data.Dataset, None), (int, None)]:
    """
    Function to prepare dataset for training and validation.
    :param dataset_config: configuration for dataset
    :param preprocess_config: configuration for preprocess
    :param mode: train or valid or test
    :param training: bool, if true, data augmentation and shuffling will be added
    :param repeat: bool, if true, dataset will be repeated, true for train/valid dataset during model.fit
    :return:
    - (data_loader_train, dataset_train, steps_per_epoch_train)
    - (data_loader_val, dataset_val, steps_per_epoch_valid)

    Cannot move this function into deepreg/dataset/util.py
    as we need DataLoader to define the output
    """
    assert mode in ["train", "valid", "test"]
    data_loader = get_data_loader(dataset_config, mode)
    if data_loader is None:
        return None, None, None
    dataset = data_loader.get_dataset_and_preprocess(
        training=training, repeat=repeat, **preprocess_config
    )
    dataset_size = data_loader.num_samples
    steps_per_epoch = max(dataset_size // preprocess_config["batch_size"], 1)
    return data_loader, dataset, steps_per_epoch


def build_log_dir(log_dir: str) -> str:
    """
    :param log_dir: str, path to where training logs to be stored.
    :return: the path of directory to save logs
    """
    log_dir = os.path.join(
        "logs", datetime.now().strftime("%Y%m%d-%H%M%S") if log_dir == "" else log_dir
    )
    if os.path.exists(log_dir):
        logging.warning("Log directory {} exists already.".format(log_dir))
    else:
        os.makedirs(log_dir)
    return log_dir


def save_array(
    save_dir: str,
    arr: (np.ndarray, tf.Tensor),
    name: str,
    gray: bool,
    save_nifti: bool = True,
    save_png: bool = True,
    overwrite: bool = True,
):
    """
    :param save_dir: path of the directory to save
    :param arr: 3D or 4D array to be saved
    :param name: name of the array, e.g. image, label, etc.
    :param gray: true if the array is between 0,1
    :param save_nifti: if true, array will be saved in nifti
    :param save_png: if true, array will be saved in png
    :param overwrite: if false, will not save the file in case the file exists
    """
    if isinstance(arr, tf.Tensor):
        arr = arr.numpy()
    if len(arr.shape) not in [3, 4]:
        raise ValueError(
            f"arr must be 3d or 4d numpy array or tf tensor, " f"got {arr}"
        )
    is_4d = len(arr.shape) == 4
    if is_4d:
        # if 4D array, it must be 3 channels
        if arr.shape[3] != 3:
            raise ValueError(
                f"4d arr must have 3 channels as last dimension, "
                f"got arr.shape = {arr.shape}"
            )

    # save in nifti format
    if save_nifti:
        nifti_file_path = os.path.join(save_dir, name + ".nii.gz")
        if overwrite or (not os.path.exists(nifti_file_path)):
            # save only if need to overwrite or doesn't exist
            os.makedirs(save_dir, exist_ok=True)
            nib.save(
                img=nib.Nifti2Image(arr, affine=np.eye(4)), filename=nifti_file_path
            )

    # save in png
    if save_png:
        png_dir = os.path.join(save_dir, name)
        dir_existed = os.path.exists(png_dir)
        for depth_index in range(arr.shape[2]):
            png_file_path = os.path.join(png_dir, f"depth{depth_index}_{name}.png")
            if overwrite or (not os.path.exists(png_file_path)):
                if not dir_existed:
                    os.makedirs(png_dir, exist_ok=True)
                plt.imsave(
                    fname=png_file_path,
                    arr=arr[:, :, depth_index, :] if is_4d else arr[:, :, depth_index],
                    vmin=0 if gray else None,
                    vmax=1 if gray else None,
                    cmap="gray" if gray else "PiYG",
                )


def calculate_metrics(
    fixed_image: tf.Tensor,
    fixed_label: (tf.Tensor, None),
    pred_fixed_image: (tf.Tensor, None),
    pred_fixed_label: (tf.Tensor, None),
    fixed_grid_ref: tf.Tensor,
    sample_index: int,
) -> dict:
    """
    Calculate image/label based metrics
    :param fixed_image: shape=(batch, f_dim1, f_dim2, f_dim3)
    :param fixed_label: shape=(batch, f_dim1, f_dim2, f_dim3) or None
    :param pred_fixed_image: shape=(batch, f_dim1, f_dim2, f_dim3)
    :param pred_fixed_label: shape=(batch, f_dim1, f_dim2, f_dim3) or None
    :param fixed_grid_ref: shape=(1, f_dim1, f_dim2, f_dim3, 3)
    :param sample_index: int,
    :return: dictionary of metrics
    """

    if pred_fixed_image is not None:
        y_true = fixed_image[sample_index : (sample_index + 1), :, :, :]
        y_pred = pred_fixed_image[sample_index : (sample_index + 1), :, :, :]
        y_true = tf.expand_dims(y_true, axis=4)
        y_pred = tf.expand_dims(y_pred, axis=4)
        ssd = image_loss.ssd(y_true=y_true, y_pred=y_pred).numpy()[0]
    else:
        ssd = None

    if fixed_label is not None and pred_fixed_label is not None:
        y_true = fixed_label[sample_index : (sample_index + 1), :, :, :]
        y_pred = pred_fixed_label[sample_index : (sample_index + 1), :, :, :]
        dice = label_loss.dice_score(y_true=y_true, y_pred=y_pred, binary=True).numpy()[
            0
        ]
        tre = label_loss.compute_centroid_distance(
            y_true=y_true, y_pred=y_pred, grid=fixed_grid_ref[0, :, :, :, :]
        ).numpy()[0]
    else:
        dice = None
        tre = None

    return dict(image_ssd=ssd, label_binary_dice=dice, label_tre=tre)


def save_metric_dict(save_dir: str, metrics: list):
    """
    :param save_dir: directory to save outputs
    :param metrics: list of dicts, dict must have key pair_index and label_index
    """
    os.makedirs(name=save_dir, exist_ok=True)

    # build dataframe
    # column is pair_index, label_index, and metrics
    df = pd.DataFrame(metrics)

    # save overall dataframe
    df.to_csv(os.path.join(save_dir, "metrics.csv"), index=False)

    # calculate mean/median/std per label
    df_per_label = df.drop(["pair_index"], axis=1)
    df_per_label = df_per_label.groupby(["label_index"])
    df_per_label = pd.concat(
        [
            df_per_label.mean().add_suffix("_mean"),
            df_per_label.median().add_suffix("_median"),
            df_per_label.std().add_suffix("_std"),
        ],
        axis=1,
        sort=True,
    )
    df_per_label = df_per_label.reindex(
        sorted(df_per_label.columns), axis=1
    )  # sort columns
    df_per_label.to_csv(
        os.path.join(save_dir, "metrics_stats_per_label.csv"), index=True
    )

    # calculate overall mean/median/std
    df_all = df.drop(["pair_index", "label_index"], axis=1)
    df_all.describe().to_csv(
        os.path.join(save_dir, "metrics_stats_overall.csv"), index=True
    )
