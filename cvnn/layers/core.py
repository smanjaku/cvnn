from abc import ABC, abstractmethod
import numpy as np
import tensorflow as tf
from tensorflow.keras.layers import Flatten, Dense, InputLayer, Layer
from tensorflow.python.keras import backend as K
from tensorflow.keras import initializers
import tensorflow_probability as tfp
from tensorflow import TensorShape, Tensor
# from keras.utils import control_flow_util
# typing
from typing import Optional, Union, List, Tuple
# Own modules
from cvnn.activations import t_activation
from cvnn.initializers import ComplexGlorotUniform, Zeros, Ones, ComplexInitializer, INIT_TECHNIQUES


t_input = Union[Tensor, tuple, list]
t_input_shape = Union[TensorShape, List[TensorShape]]

DEFAULT_COMPLEX_TYPE = tf.as_dtype(np.complex64)


class ComplexLayer(ABC):

    @abstractmethod
    def get_real_equivalent(self):
        """
        :return: Gets a real-valued COPY of the Complex Layer.
        """
        pass


def complex_input(shape=None, batch_size=None, name=None, dtype=DEFAULT_COMPLEX_TYPE,
                  sparse=False, tensor=None, ragged=False, **kwargs):
    """
    `complex_input()` is used to instantiate a Keras tensor.
    A Keras tensor is a TensorFlow symbolic tensor object,
    which we augment with certain attributes that allow us to build a Keras model
    just by knowing the inputs and outputs of the model.
    For instance, if `a`, `b` and `c` are Keras tensors,
    it becomes possible to do:
    `model = Model(input=[a, b], output=c)`
    Arguments:
      shape: A shape tuple (integers), not including the batch size.
          For instance, `shape=(32,)` indicates that the expected input
          will be batches of 32-dimensional vectors. Elements of this tuple
          can be None; 'None' elements represent dimensions where the shape is
          not known.
      batch_size: optional static batch size (integer).
      name: An optional name string for the layer.
          Should be unique in a model (do not reuse the same name twice).
          It will be autogenerated if it isn't provided.
      dtype: The data type expected by the input
      sparse: A boolean specifying whether the placeholder to be created is
          sparse. Only one of 'ragged' and 'sparse' can be True. Note that,
          if `sparse` is False, sparse tensors can still be passed into the
          input - they will be densified with a default value of 0.
      tensor: Optional existing tensor to wrap into the `Input` layer.
          If set, the layer will use the `tf.TypeSpec` of this tensor rather
          than creating a new placeholder tensor.
      ragged: A boolean specifying whether the placeholder to be created is
          ragged. Only one of 'ragged' and 'sparse' can be True. In this case,
          values of 'None' in the 'shape' argument represent ragged dimensions.
          For more information about RaggedTensors, see
          [this guide](https://www.tensorflow.org/guide/ragged_tensors).
      **kwargs: deprecated arguments support. Supports `batch_shape` and
          `batch_input_shape`.
    Returns:
        A `tensor`.
    Example:
    ```python
        # this is a logistic regression in Keras
        x = complex_input(shape=(32,))
        y = Dense(16, activation='softmax')(x)
        model = Model(x, y)
    ```
    Note that even if eager execution is enabled,
    `Input` produces a symbolic tensor (i.e. a placeholder).
    This symbolic tensor can be used with other
    TensorFlow ops, as such:
    ```python
        x = complex_input(shape=(32,))
        y = tf.square(x)
    ```
    Raises:
        ValueError: If both `sparse` and `ragged` are provided.
        ValueError: If both `shape` and (`batch_input_shape` or `batch_shape`) are provided.
        ValueError: If both `shape` and `tensor` are None.
        ValueError: if any unrecognized parameters are provided.
    """
    if sparse and ragged:
        raise ValueError(
            'Cannot set both sparse and ragged to True in a Keras input.')

    dtype = tf.as_dtype(dtype)
    input_layer_config = {'name': name, 'dtype': dtype.name, 'sparse': sparse,
                          'ragged': ragged, 'input_tensor': tensor}

    batch_input_shape = kwargs.pop('batch_input_shape',
                                   kwargs.pop('batch_shape', None))
    if shape is not None and batch_input_shape is not None:
        raise ValueError('Only provide the `shape` OR `batch_input_shape` argument '
                         'to Input, not both at the same time.')
    if batch_input_shape is None and shape is None and tensor is None:
        raise ValueError('Please provide to Input either a `shape`'
                         ' or a `tensor` argument. Note that '
                         '`shape` does not include the batch '
                         'dimension.')
    if kwargs:
        raise ValueError('Unrecognized keyword arguments:', kwargs.keys())

    if batch_input_shape:
        shape = batch_input_shape[1:]
        input_layer_config.update({'batch_input_shape': batch_input_shape})
    else:
        input_layer_config.update(
            {'batch_size': batch_size, 'input_shape': shape})
    # import pdb; pdb.set_trace()
    input_layer = ComplexInput(**input_layer_config)

    # Return tensor including `_keras_history`.
    # Note that in this case train_output and test_output are the same pointer.
    outputs = input_layer._inbound_nodes[0].output_tensors
    if isinstance(outputs, list) and len(outputs) == 1:
        return outputs[0]
    else:
        return outputs


class ComplexInput(InputLayer, ComplexLayer):

    def __init__(self, input_shape=None, batch_size=None, dtype=DEFAULT_COMPLEX_TYPE, input_tensor=None, sparse=False,
                 name=None, ragged=False, **kwargs):
        super(ComplexInput, self).__init__(input_shape=input_shape, batch_size=batch_size, dtype=dtype,
                                           input_tensor=input_tensor, sparse=sparse,
                                           name=name, ragged=ragged, **kwargs
                                           )

    def get_real_equivalent(self):
        real_input_shape = self.input_shape[:-1] + (self.input_shape[-1] * 2,)
        return ComplexInput(input_shape=real_input_shape, batch_size=self.batch_size, dtype=self.dtype,
                            input_tensor=self.input_tensor, sparse=self.sparse, name=self.name + "_real_equiv",
                            ragged=self.ragged)


class ComplexFlatten(Flatten, ComplexLayer):

    def call(self, inputs: t_input):
        # tf.print(f"inputs at ComplexFlatten are {inputs.dtype}")
        real_flat = super(ComplexFlatten, self).call(tf.math.real(inputs))
        imag_flat = super(ComplexFlatten, self).call(tf.math.imag(inputs))
        return tf.cast(tf.complex(real_flat, imag_flat), inputs.dtype)  # Keep input dtype

    def get_real_equivalent(self):
        # Dtype agnostic so just init one.
        return ComplexFlatten(name=self.name + "_real_equiv")


class ComplexDense(Dense, ComplexLayer):
    """
    Fully connected complex-valued layer.

    Implements the operation:
        activation(input * weights + bias)

    * where data types can be either complex or real.
    * activation is the element-wise activation function passed as the activation argument,
    * weights is a matrix created by the layer
    * bias is a bias vector created by the layer
    """

    def __init__(self, units: int, activation: t_activation = None, use_bias: bool = True,
                 kernel_initializer="ComplexGlorotUniform",
                 bias_initializer="Zeros",
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 dtype=DEFAULT_COMPLEX_TYPE,  # TODO: Check typing of this.
                 init_technique: str = 'mirror',
                 **kwargs):
        """
        :param units: Positive integer, dimensionality of the output space.
        :param activation: Activation function to use.
            Either from keras.activations or cvnn.activations. For complex dtype, only cvnn.activations module supported.
            If you don't specify anything, no activation is applied (ie. "linear" activation: a(x) = x).
        :param use_bias: Boolean, whether the layer uses a bias vector.
        :param kernel_initializer: Initializer for the kernel weights matrix.
            Recommended to use a `ComplexInitializer` such as `cvnn.initializers.ComplexGlorotUniform()` (default)
        :param bias_initializer: Initializer for the bias vector.
            Recommended to use a `ComplexInitializer` such as `cvnn.initializers.Zeros()` (default)
        :param dtype: Dtype of the input and layer.
        :param init_technique: One of 'mirror' or 'zero_imag'. Tells the initializer how to init complex number if
            the initializer was tensorflow's built in initializers (not supporting complex numbers).
            - 'mirror': Uses the initializer for both real and imaginary part.
                Note that some initializers such as Glorot or He will lose it's property if initialized this way.
            - 'zero_imag': Initializer real part and let imaginary part to zero.
        """
        # TODO: verify the initializers? and that dtype complex has cvnn.activations.
        if activation is None:
            activation = "linear"
        super(ComplexDense, self).__init__(units, activation=activation, use_bias=use_bias,
                                           kernel_initializer=kernel_initializer,
                                           bias_initializer=bias_initializer,
                                           kernel_constraint=kernel_constraint, kernel_regularizer=kernel_regularizer,
                                           **kwargs)
        # !Cannot override dtype of the layer because it has a read-only @property
        self.my_dtype = tf.dtypes.as_dtype(dtype)
        self.init_technique = init_technique.lower()

    def build(self, input_shape):
        if self.my_dtype.is_complex:
            i_kernel_dtype = self.my_dtype if isinstance(self.kernel_initializer,
                                                         ComplexInitializer) else self.my_dtype.real_dtype
            i_bias_dtype = self.my_dtype if isinstance(self.bias_initializer,
                                                       ComplexInitializer) else self.my_dtype.real_dtype
            i_kernel_initializer = self.kernel_initializer
            i_bias_initializer = self.bias_initializer
            if not isinstance(self.kernel_initializer, ComplexInitializer):
                tf.print(f"WARNING: you are using a Tensorflow Initializer for complex numbers. "
                         f"Using {self.init_technique} method.")
                if self.init_technique in INIT_TECHNIQUES:
                    if self.init_technique == 'zero_imag':
                        # This section is done to initialize with tf initializers, making imaginary part zero
                        i_kernel_initializer = initializers.Zeros()
                        i_bias_initializer = initializers.Zeros()
                else:
                    raise ValueError(f"Unsuported init_technique {self.init_technique}, "
                                     f"supported techniques are {INIT_TECHNIQUES}")

            self.w_r = self.add_weight('kernel_r',
                                     shape=(input_shape[-1], self.units),
                                     dtype=self.my_dtype.real_dtype,
                                     initializer=self.kernel_initializer,
                                     trainable=True,
                                     constraint=self.kernel_constraint, regularizer=self.kernel_regularizer)
            #self.w_r = tf.Variable(
            #    name='kernel_r',
            #    initial_value=self.kernel_initializer(shape=(input_shape[-1], self.units), dtype=i_kernel_dtype),
            #    trainable=True
            #)
            self.w_i = self.add_weight('kernel_i',
                                     shape=(input_shape[-1], self.units),
                                     dtype=self.my_dtype.real_dtype,
                                     initializer=self.kernel_initializer,
                                     trainable=True,
                                     constraint=self.kernel_constraint, regularizer=self.kernel_regularizer)
            #self.w_i = tf.Variable(
            #    name='kernel_i',
            #    initial_value=i_kernel_initializer(shape=(input_shape[-1], self.units), dtype=i_kernel_dtype),
            #    trainable=True
            #)
            if self.use_bias:
                self.b_r = tf.Variable(
                    name='bias_r',
                    initial_value=self.bias_initializer(shape=(self.units,), dtype=i_bias_dtype),
                    trainable=self.use_bias
                )
                self.b_i = tf.Variable(
                    name='bias_i',
                    initial_value=i_bias_initializer(shape=(self.units,), dtype=i_bias_dtype),
                    trainable=self.use_bias
                )
        else:
            # TODO: For Complex you should probably want to use MY init for real keras. DO sth! at least error message
            self.w = self.add_weight('kernel',
                                     shape=(input_shape[-1], self.units),
                                     dtype=self.my_dtype,
                                     initializer=self.kernel_initializer,
                                     trainable=True,
                                     constraint=self.kernel_constraint, regularizer=self.kernel_regularizer)
            if self.use_bias:
                self.b = self.add_weight('bias', shape=(self.units,), dtype=self.my_dtype,
                                         initializer=self.bias_initializer, trainable=self.use_bias)

    def call(self, inputs: t_input):
        # tf.print(f"inputs at ComplexDense are {inputs.dtype}")
        if inputs.dtype != self.my_dtype:
            tf.print(f"WARNING: {self.name} - Expected input to be {self.my_dtype}, but received {inputs.dtype}.")
            if self.my_dtype.is_complex and inputs.dtype.is_floating:
                tf.print("\tThis is normally fixed using ComplexInput() "
                         "at the start (tf casts input automatically to real).")
            inputs = tf.cast(inputs, self.my_dtype)
        if self.my_dtype.is_complex:
            w = tf.complex(self.w_r, self.w_i)
            if self.use_bias:
                b = tf.complex(self.b_r, self.b_i)
        else:
            w = self.w
            if self.use_bias:
                b = self.b
        out = tf.matmul(inputs, w)
        if self.use_bias:
            out = out + b
        return self.activation(out)

    def get_real_equivalent(self, output_multiplier=2):
        # assert self.my_dtype.is_complex, "The layer was already real!"    # TODO: Shall I check this?
        # TODO: Does it pose a problem not to re-create an object of the initializer?
        return ComplexDense(units=int(round(self.units * output_multiplier)),
                            activation=self.activation, use_bias=self.use_bias,
                            kernel_initializer=self.kernel_initializer, bias_initializer=self.bias_initializer,
                            kernel_constraint=self.kernel_constraint, kernel_regularizer=self.kernel_regularizer, #MODIFIED CODE ------
                            dtype=self.my_dtype.real_dtype, name=self.name + "_real_equiv")

    def get_config(self):
        config = super(ComplexDense, self).get_config()
        config.update({
            'dtype': self.my_dtype,
            'init_technique': self.init_technique

        })
        return config


class ComplexDropout(Layer, ComplexLayer):
    """
    Applies Dropout to the input.
    It works also with complex inputs!
    The Dropout layer randomly sets input units to 0 with a frequency of `rate`
    at each step during training time, which helps prevent overfitting.
    Inputs not set to 0 are scaled up by 1/(1 - rate) such that the sum over
    all inputs is unchanged.
    Note that the Dropout layer only applies when `training` is set to True
    such that no values are dropped during inference. When using `model.fit`,
    `training` will be appropriately set to True automatically, and in other
    contexts, you can set the kwarg explicitly to True when calling the layer.
    (This is in contrast to setting `trainable=False` for a Dropout layer.
    `trainable` does not affect the layer's behavior, as Dropout does
    not have any variables/weights that can be frozen during training.)
    """

    def __init__(self, rate: float, noise_shape=None, seed: Optional[int] = None, **kwargs):
        """
        :param rate: Float between 0 and 1. Fraction of the input units to drop.
        :param noise_shape: 1D integer tensor representing the shape of the binary dropout mask that
            will be multiplied with the input.
            For instance, if your inputs have shape `(batch_size, timesteps, features)` and you want the dropout
            mask to be the same for all timesteps, you can use `noise_shape=(batch_size, 1, features)`.
        :param seed: A Python integer to use as random seed.
        """
        super(ComplexDropout, self).__init__(**kwargs)  # trainable=False,
        if isinstance(rate, (int, float)) and not 0 <= rate <= 1:
            raise ValueError(f'Invalid value {rate} received for `rate`, expected a value between 0 and 1.')
        self.rate = rate
        self.seed = seed
        self.noise_shape = noise_shape

    def _get_noise_shape(self, inputs):
        # Subclasses of `Dropout` may implement `_get_noise_shape(self, inputs)`,
        # which will override `self.noise_shape`, and allows for custom noise
        # shapes with dynamically sized inputs.
        if self.noise_shape is None:
            return None

        concrete_inputs_shape = tf.shape(inputs)
        noise_shape = []
        for i, value in enumerate(self.noise_shape):
            noise_shape.append(concrete_inputs_shape[i] if value is None else value)
        return tf.convert_to_tensor(noise_shape)

    def call(self, inputs, training=None):
        """
        :param inputs: Input tensor (of any rank).
        :param training: Python boolean indicating whether the layer should behave in training mode (adding dropout)
            or in inference mode (doing nothing).
        """
        if training is None:
            training = K.learning_phase()
            tf.print(f"Training was None and now is {training}")
            # This is used for my own debugging, I don't know WHEN this happens,
            # I trust K.learning_phase() returns a correct boolean.

        # def dropped_inputs():
        #     # import pdb; pdb.set_trace()
        #     drop_filter = tf.nn.dropout(tf.ones(tf.shape(inputs)), rate=self.rate,
        #                                 noise_shape=self._get_noise_shape(inputs), seed=self.seed)
        #     y_out = tf.multiply(tf.cast(drop_filter, dtype=inputs.dtype), inputs)
        #     y_out = tf.cast(y_out, dtype=inputs.dtype)
        #     return y_out
        # output = control_flow_util.smart_cond(training, dropped_inputs, lambda: tf.identity(inputs))
        # return output
        if not training:
            return inputs
        drop_filter = tf.nn.dropout(tf.ones(tf.shape(inputs)), rate=self.rate,
                                    noise_shape=self.noise_shape, seed=self.seed)
        y_out = tf.multiply(tf.cast(drop_filter, dtype=inputs.dtype), inputs)
        y_out = tf.cast(y_out, dtype=inputs.dtype)
        return y_out

    def compute_output_shape(self, input_shape):
        return input_shape

    def get_real_equivalent(self):
        return ComplexDropout(rate=self.rate, seed=self.seed, noise_shape=self.noise_shape,
                              name=self.name + "_real_equiv")

    def get_config(self):
        config = super(ComplexDropout, self).get_config()
        config.update({
            'rate': self.rate,
            'noise_shape': self.noise_shape,
            'seed': self.seed
        })
        return config


class ComplexBatchNormalization(Layer, ComplexLayer):
    """
    Complex Batch-Normalization as defined in section 3.5 of https://arxiv.org/abs/1705.09792
    """

    def __init__(self, axis: Union[List[int], Tuple[int], int] = -1, momentum: float = 0.99,
                 center: bool = True, scale: bool = True, epsilon: float = 0.001,
                 beta_initializer=Zeros(), gamma_initializer=Ones(), dtype=DEFAULT_COMPLEX_TYPE,
                 moving_mean_initializer=Zeros(), moving_variance_initializer=Ones(), cov_method: int = 2,  # TODO: Check inits
                 **kwargs):
        self.my_dtype = tf.dtypes.as_dtype(dtype)
        self.epsilon = epsilon
        self.cov_method = cov_method
        if isinstance(axis, int):
            axis = [axis]
        self.axis = list(axis)
        super(ComplexBatchNormalization, self).__init__(**kwargs)
        self.momentum = momentum
        self.beta_initializer = initializers.get(beta_initializer)
        self.gamma_initializer = initializers.get(gamma_initializer)
        self.moving_mean_initializer = initializers.get(moving_mean_initializer)
        self.moving_variance_initializer = initializers.get(moving_variance_initializer)
        self.center = center
        self.scale = scale

    def build(self, input_shape):
        self.epsilon_matrix = tf.eye(2, dtype=self.my_dtype.real_dtype) * self.epsilon
        # Cast the negative indices to positive
        self.axis = [len(input_shape) + ax if ax < 0 else ax for ax in self.axis]
        self.used_axis = [ax for ax in range(0, len(input_shape)) if ax not in self.axis]
        desired_shape = [input_shape[ax] for ax in self.axis]
        if self.my_dtype.is_complex:
            self.gamma_r = tf.Variable(
                name='gamma_r',
                initial_value=self.gamma_initializer(shape=tuple(desired_shape), dtype=self.my_dtype),
                trainable=True
            )
            self.gamma_i = tf.Variable(
                name='gamma_i',
                initial_value=Zeros()(shape=tuple(desired_shape), dtype=self.my_dtype),
                trainable=True
            )  # I think I just need to scale with gamma, so by default I leave the imag part to zero
            self.beta_r = tf.Variable(
                name="beta_r",
                initial_value=self.beta_initializer(shape=desired_shape, dtype=self.my_dtype),
                trainable=True
            )
            self.beta_i = tf.Variable(
                name="beta_i",
                initial_value=self.beta_initializer(shape=desired_shape, dtype=self.my_dtype),
                trainable=True
            )
            self.moving_mean = tf.Variable(
                name='moving_mean',
                initial_value=tf.complex(real=self.moving_mean_initializer(shape=desired_shape,
                                                                           dtype=self.my_dtype),
                                         imag=self.moving_mean_initializer(shape=desired_shape,
                                                                           dtype=self.my_dtype)),
                trainable=False
            )
            self.moving_var = tf.Variable(
                name='moving_var',
                initial_value=tf.eye(2) * self.moving_variance_initializer(shape=tuple(desired_shape) + (2, 2),
                                                                           dtype=self.my_dtype) / tf.math.sqrt(2.),
                trainable=False
            )
        else:
            self.gamma = tf.Variable(
                name='gamma',
                initial_value=self.gamma_initializer(shape=tuple(desired_shape), dtype=self.my_dtype),
                trainable=True
            )
            self.beta = tf.Variable(
                name="beta",
                initial_value=self.beta_initializer(shape=desired_shape, dtype=self.my_dtype),
                trainable=True
            )
            self.moving_mean = tf.Variable(
                name='moving_mean',
                initial_value=self.moving_mean_initializer(shape=desired_shape, dtype=self.my_dtype),
                trainable=False
            )
            self.moving_var = tf.Variable(
                name='moving_var',
                initial_value=tf.eye(2, dtype=self.my_dtype) * self.moving_variance_initializer(
                    shape=tuple(desired_shape) + (2, 2),
                    dtype=self.my_dtype),
                trainable=False
            )

    def call(self, inputs, training=None):
        if inputs.dtype != self.my_dtype:
            tf.print(f"Warning: Expecting input dtype {self.my_dtype} but got {inputs.dtype}. "
                     f"Automatic cast will be done.")
            inputs = tf.cast(inputs, dtype=self.my_dtype)
        if training is None:
            training = K.learning_phase()
            tf.print(f"Training was None and now is {training}")
            # This is used for my own debugging, I don't know WHEN this happens,
            # I trust K.learning_phase() returns a correct boolean.
        if training:
            # First get the mean and var
            mean = tf.math.reduce_mean(inputs, axis=self.used_axis)
            if self.cov_method == 1:
                X_20 = tf.concat((tf.math.real(inputs), tf.math.imag(inputs)), axis=-1)
                var_20_20 = tfp.stats.covariance(X_20, sample_axis=self.used_axis, event_axis=-1)
                valu = int(var_20_20.shape[-1] / 2)
                indices = [([[i, i], [i, i + valu]], [[i + valu, i], [i + valu, i + valu]]) for i in range(0, valu)]
                var = tf.gather_nd(var_20_20, indices=indices)
            elif self.cov_method == 2:
                X_10_2 = tf.stack((tf.math.real(inputs), tf.math.imag(inputs)), axis=-1)
                var_10_2_2 = tfp.stats.covariance(X_10_2, sample_axis=self.used_axis, event_axis=-1)
                var = var_10_2_2
            else:
                raise ValueError(f"Method {self.method} not implemented")

            # Now the train part with these values
            self.moving_mean.assign(self.momentum * self.moving_mean + (1. - self.momentum) * mean)
            self.moving_var.assign(self.moving_var * self.momentum + var * (1. - self.momentum))
            out = self._normalize(inputs, var, mean)
        else:
            out = self._normalize(inputs, self.moving_var, self.moving_mean)
        if self.scale:
            if self.my_dtype.is_complex:
                gamma = tf.complex(self.gamma_r, self.gamma_i)
            else:
                gamma = self.gamma
            out = gamma * out
        if self.center:
            if self.my_dtype.is_complex:
                beta = tf.complex(self.beta_r, self.beta_i)
            else:
                beta = self.beta
            out = out + beta
        return out

    def _normalize(self, inputs, var, mean):
        """
        :inputs: Tensor
        :param var: Tensor of shape [..., 2, 2], if inputs dtype is real, var[slice] = [[var_slice, 0], [0, 0]]
        :param mean: Tensor with the mean in the corresponding dtype (same shape as inputs)
        """
        complex_zero_mean = inputs - mean
        # Inv and sqrtm is done over 2 inner most dimension [..., M, M] so it should be [..., 2, 2] for us.
        inv_sqrt_var = tf.linalg.sqrtm(tf.linalg.inv(var + self.epsilon_matrix))  # var^(-1/2)  # TODO: Check this exists always?
        # Separate real and imag so I go from shape [...] to [..., 2]
        zero_mean = tf.stack((tf.math.real(complex_zero_mean), tf.math.imag(complex_zero_mean)), axis=-1)
        # I expand dims to make the mult of matrix [..., 2, 2] and [..., 2, 1] resulting in [..., 2, 1]
        inputs_hat = tf.matmul(inv_sqrt_var, tf.expand_dims(zero_mean, axis=-1))

        # Then I squeeze to remove the last shape so I go from [..., 2, 1] to [..., 2].
        # Use reshape and not squeeze in case I have 1 channel for example.
        squeeze_inputs_hat = tf.reshape(inputs_hat, shape=tf.shape(inputs_hat)[:-1])
        # Get complex data
        complex_inputs_hat = tf.cast(tf.complex(squeeze_inputs_hat[..., 0], squeeze_inputs_hat[..., 1]),
                                     dtype=self.my_dtype)
        # import pdb; pdb.set_trace()
        return complex_inputs_hat

    """@staticmethod
    def _normalize_real(inputs, var, mean):
        numerator = inputs - mean
        denominator = tf.math.sqrt(var[..., 0, 0])
        return numerator / tf.cast(denominator, dtype=inputs.dtype)"""

    def get_real_equivalent(self):
        return ComplexBatchNormalization(axis=self.axis, momentum=self.momentum, center=self.center, scale=self.scale,
                                         beta_initializer=self.beta_initializer, epsilon=self.epsilon_matrix[0],
                                         gamma_initializer=self.gamma_initializer, dtype=self.my_dtype,
                                         moving_mean_initializer=self.moving_mean_initializer,
                                         moving_variance_initializer=self.moving_variance_initializer)

    def get_config(self):
        config = super(ComplexBatchNormalization, self).get_config()
        config.update({
            'axis': self.axis,
            'momentum': self.momentum,
            'center': self.center,
            'scale': self.scale,
            'beta_initializer': self.beta_initializer,
            'gamma_initializer': self.gamma_initializer,
            'dtype': self.my_dtype,
            'moving_mean_initializer': self.moving_mean_initializer,
            'moving_variance_initializer': self.moving_variance_initializer
        })
        return config
