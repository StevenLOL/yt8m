import math

import tensorflow as tf

from tensorflow.contrib.rnn.python.ops import core_rnn_cell
from yt8m.models import models
import attn
from tensorflow.contrib.rnn.python.ops import gru_ops

slim = tf.contrib.slim

def moe_layer(model_input, hidden_size, num_mixtures,
                act_func=None, l2_penalty=None):
  gate_activations = slim.fully_connected(
      model_input,
      hidden_size * (num_mixtures + 1),
      activation_fn=None,
      biases_initializer=None,
      weights_regularizer=slim.l2_regularizer(l2_penalty),
      scope="gates")
  expert_activations = slim.fully_connected(
      model_input,
      hidden_size * num_mixtures,
      activation_fn=None,
      weights_regularizer=slim.l2_regularizer(l2_penalty),
      scope="experts")

  expert_act_func = act_func
  gating_distribution = tf.nn.softmax(tf.reshape(
      gate_activations,
      [-1, num_mixtures + 1]))  # (Batch * #Labels) x (num_mixtures + 1)
  expert_distribution = tf.reshape(
      expert_activations,
      [-1, num_mixtures])  # (Batch * #Labels) x num_mixtures
  if expert_act_func is not None:
    expert_distribution = expert_act_func(expert_distribution)

  outputs = tf.reduce_sum(
      gating_distribution[:, :num_mixtures] * expert_distribution, 1)
  outputs = tf.reshape(outputs, [-1, hidden_size])
  return outputs

class GRUAttn2Layers(models.BaseModel):
  def __init__(self):
    super(GRUAttn2Layers, self).__init__()
    self.cell_size = 512
    self.max_steps = 300

    self.normalize_input = True # TODO
    self.clip_global_norm = 5
    self.var_moving_average_decay = 0.9997
    self.optimizer_name = "AdamOptimizer"
    self.base_learning_rate = 3e-4

  def create_model(self, model_input, vocab_size, num_frames,
                   is_training=True, dense_labels=None, **unused_params):
    self.is_training = is_training
    num_frames = tf.cast(tf.expand_dims(num_frames, 1), tf.float32)

    runtime_batch_size = tf.shape(model_input)[0]

    with tf.variable_scope("EncLayer0"):
      enc_cell = self.get_enc_cell(self.cell_size)
      initial_state = enc_cell.zero_state(runtime_batch_size, dtype=tf.float32)
      enc_outputs, enc_state = tf.nn.dynamic_rnn(
          enc_cell, model_input, initial_state=initial_state, scope="enc0")

      flatten_outputs = attn.attn(enc_outputs, fea_size=512)

    if self.is_training:
      flatten_outputs = tf.nn.dropout(flatten_outputs, 0.8)
    logits = moe_layer(flatten_outputs, vocab_size, 2,
                       act_func=tf.nn.sigmoid, l2_penalty=1e-8)
    # slim.fully_connected(logits, vocab_size, activation_fn=tf.nn.sigmoid,
                         # l2_penalty=1e-8)

    return {"predictions": logits}

  def get_enc_cell(self, cell_size):
    cells = []
    cell = gru_ops.GRUBlockCell(cell_size)
    if self.is_training:
      cell = tf.contrib.rnn.DropoutWrapper(cell, output_keep_prob=0.5)
    cells.append(cell)

    cell = gru_ops.GRUBlockCell(cell_size)
    cell = core_rnn_cell.OutputProjectionWrapper(cell, 512)
    cells.append(cell)

    cell = tf.contrib.rnn.MultiRNNCell(
        cells,
        state_is_tuple=False)

    return cell
