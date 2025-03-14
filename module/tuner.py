
from typing import Any, Dict, NamedTuple, Text
import keras_tuner as kt
import tensorflow as tf
import tensorflow_transform as tft
from keras_tuner.engine import base_tuner
from tensorflow.keras import layers
from tfx.components.trainer.fn_args_utils import FnArgs
from tensorflow.keras.layers import LeakyReLU

LABEL_KEY = 'oh_label'
FEATURE_KEY = 'Text'
VOCAB_SIZE = 10000
epochs = 5

TunerFnResult = NamedTuple(
    "TunerFnResult", [("tuner", base_tuner.BaseTuner), ("fit_kwargs", Dict[Text, Any])]
)

def transformed_name(key):
    return key + "_xf"

def gzip_reader_fn(filenames):
    return tf.data.TFRecordDataset(filenames, compression_type='GZIP')

def input_fn(
    file_pattern, 
    tf_transform_output, 
    num_epochs, 
    batch_size=64) -> tf.data.Dataset:

    # Get post_transform feature spec
    transform_feature_spec = (
        tf_transform_output.transformed_feature_spec().copy()
    )

    # Create bacthes of data
    dataset = tf.data.experimental.make_batched_features_dataset(
        file_pattern = file_pattern,
        batch_size = batch_size,
        features = transform_feature_spec,
        reader = gzip_reader_fn,
        num_epochs = num_epochs,
        label_key = transformed_name(LABEL_KEY)
    ).repeat(epochs)

    return dataset
 

def model_builder(hp, vectorizer):
    ### Define parameter yang digunakan untuk tuning
    num_layer = hp.Int("num_layer", min_value=1, max_value=5, step=1)
    embed_dim = hp.Int("embed_dim", min_value=16, max_value=128, step=32)
    fc_layer = hp.Int("fc_layer", min_value=32, max_value=64, step=16)
    lr = hp.Choice("lr", values=[1e-2, 1e-3, 1e-4])
    # lstm_units = hp.Int("lstm_units", min_value=32, max_value=128, step=32)

    inputs = tf.keras.Input(shape=(1,), name=transformed_name(FEATURE_KEY), dtype=tf.string)
    reshaped_narrative = tf.reshape(inputs, [-1])
    x = vectorizer(reshaped_narrative)
    x = layers.Embedding(VOCAB_SIZE, embed_dim, name="embedding")(x)
    x = layers.GlobalAveragePooling1D()(x)
    x = layers.Dropout(0.2)(x)
    # x = layers.Bidirectional(layers.LSTM(lstm_units))(x)
    for _ in range(num_layer):
        x = layers.Dense(fc_layer, activation='relu')(x)
    # x = layers.Dense(fc_layer, activation='relu')(x)
    # x = layers.Dense(fc_layer, activation="relu")(x)
    
    x = layers.Dropout(0.2)(x)
    outputs = layers.Dense(1, activation='sigmoid')(x)

    model = tf.keras.Model(inputs=inputs, outputs=outputs)
    model.compile(
        loss = tf.keras.losses.BinaryCrossentropy(),
        optimizer = tf.keras.optimizers.Adam(learning_rate=lr),
        metrics = [tf.keras.metrics.BinaryAccuracy()]
    )

    model.summary()
    return model

def tuner_fn(fn_args):
    # Membuat training dan validation datasetyang telah di preprocessing
    tf_transform_output = tft.TFTransformOutput(fn_args.transform_graph_path)
    train_set = input_fn(fn_args.train_files[0], tf_transform_output, num_epochs=epochs)
    val_set = input_fn(fn_args.eval_files[0], tf_transform_output, num_epochs=epochs)

    vectorize_layer = layers.TextVectorization(
        standardize = "lower_and_strip_punctuation",
        max_tokens = 200,
        output_mode = 'int',
        output_sequence_length = 100
    )

    vectorize_layer.adapt(train_set.map(lambda x, _: x[transformed_name(FEATURE_KEY)]))

    # Callback untuk early stopping
    stop_early = tf.keras.callbacks.EarlyStopping(
        monitor='val_binary_accuracy', 
        mode='max', 
        verbose=1, 
        patience=15
    )

    # Mendefinisikan strategi hyperparameter tuning
    # tuner = kt.RandomSearch(
    #     hypermodel = lambda hp: model_builder(hp, vectorize_layer),
    #     objective = 'val_sparse_categorical_accuracy',
    #     max_trials = epochs,
    #     seed = 28,
    # )
    tuner = kt.Hyperband(
        lambda hp: model_builder(hp, vectorize_layer),
        objective = 'val_binary_accuracy',
        max_epochs = epochs,
        factor = 3,
        directory = fn_args.working_dir,
        project_name = 'kt_hyperband'
    )

    return TunerFnResult(
        tuner = tuner,
        fit_kwargs = {
            "callbacks": [stop_early],
            'x' : train_set,
            'validation_data': val_set,
            'steps_per_epoch': fn_args.train_steps,
            'validation_steps': fn_args.eval_steps
        }
    )
