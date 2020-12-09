from abc import ABC, abstractmethod
from itertools import count
import tensorflow as tf
from collections.abc import Iterable
import sys
import numpy as np
from pdb import set_trace
# My package
from cvnn.activation_functions import apply_activation
from cvnn.utils import get_func_name
from cvnn import logger
from time import time
import cvnn.initializers as initializers
# Typing
from tensorflow import dtypes
from numpy import dtype, ndarray
from typing import Union, Callable, Optional, List, Set, Tuple
from cvnn.initializers import RandomInitializer
from cvnn.activation_functions import t_activation

REAL = (np.float32,)
COMPLEX = (np.complex64,)
SUPPORTED_DTYPES = REAL + COMPLEX  # , np.complex128, np.float64) Gradients return None when complex128
layer_count = count(0)  # Used to count the number of layers

t_input_shape = Union[int, tuple, list]
t_kernel_shape = t_input_shape
t_kernel_2_shape = Union[int, Tuple[int, int], Tuple[int, int, int, int], List[int]]
t_stride_shape = t_input_shape
t_padding_shape = Union[str, t_input_shape]
t_Callable_shape = Union[t_input_shape, Callable]  # Either a input_shape or a function that sets self.output
t_Dtype = Union[dtypes.DType, dtype]

PADDING_MODES = {
    "valid",
    "same",
    "full"
}

DATA_FORMAT = {
    "channels_last",
    "channels_first"
}


class ComplexLayer(ABC):
    # Being ComplexLayer an abstract class, then this can be called using:
    #   self.__class__.__bases__.<variable>
    # As all child's will have this class as base, mro gives a full list so won't work.
    last_layer_output_dtype = None  # TODO: Make it work both with np and tf dtypes
    last_layer_output_size = None

    def __init__(self, output_size: t_Callable_shape, input_size=Optional[t_Callable_shape], input_dtype=t_Dtype,
                 *args):
        """
        Base constructor for a complex layer. The first layer will need a input_dtype and input_size.
        For the other classes is optional,
            if input_size or input_dtype does not match last layer it will throw a warning
        :param output_size: Output size of the layer.
            If the output size depends on the input_size, a function must be passed as output_size.
        :param input_size: Input size of the layer
        :param input_dtype: data type of the input
        """
        if output_size is None:
            logger.error("Output size = None not supported")
            sys.exit(-1)

        if input_dtype is None and self.__class__.__bases__[0].last_layer_output_dtype is None:
            # None input dtype given but it's the first layer declared
            logger.error("First layer must be given an input dtype", exc_info=True)
            sys.exit(-1)
        elif input_dtype is None and self.__class__.__bases__[0].last_layer_output_dtype is not None:
            # Use automatic mode
            self.input_dtype = self.__class__.__bases__[0].last_layer_output_dtype
        elif input_dtype is not None:
            if input_dtype not in SUPPORTED_DTYPES:
                logger.error("Layer::__init__: unsupported input_dtype " + str(input_dtype), exc_info=True)
                sys.exit(-1)
            if self.__class__.__bases__[0].last_layer_output_dtype is not None:
                if self.__class__.__bases__[0].last_layer_output_dtype != input_dtype:
                    logger.warning("Input dtype " + str(input_dtype) +
                                   " is not equal to last layer's input dtype " +
                                   str(self.__class__.__bases__[0].last_layer_output_dtype))
            self.input_dtype = np.dtype(input_dtype)

        # This will be normally the case.
        # Each layer must change this value if needed.
        self.__class__.__bases__[0].last_layer_output_dtype = self.input_dtype

        # Input Size
        if input_size is None:
            if self.__class__.__bases__[0].last_layer_output_size is None:
                # None input size given but it's the first layer declared
                logger.error("First layer must be given an input size")
                sys.exit(-1)
            else:  # self.__class__.__bases__[0].last_layer_output_dtype is not None:
                self.input_size = self.__class__.__bases__[0].last_layer_output_size
        elif input_size is not None:
            if callable(input_size):
                input_size()
                assert self.input_size, "Error: input_size function must set self.input_size"
            else:
                self.input_size = input_size
            if self.__class__.__bases__[0].last_layer_output_size is not None:
                if input_size != self.__class__.__bases__[0].last_layer_output_size:
                    logger.warning(f"Input size {input_size} is not equal to last layer's output size "
                                   f"{self.__class__.__bases__[0].last_layer_output_size}")

        if callable(output_size):
            output_size(*args)
            assert self.output_size, "Error: output_size function must set self.output_size"
        else:
            self.output_size = output_size
        for x in self.__class__.mro():
            if x == ComplexLayer:
                x.last_layer_output_size = self.output_size
        # self.__class__.__bases__[0].last_layer_output_size = self.output_size
        self.layer_number = next(layer_count)  # Know it's own number
        self.__class__.__call__ = self.call  # Make my object callable

    @abstractmethod
    def __deepcopy__(self, memodict=None):
        pass

    def get_input_dtype(self):
        return self.input_dtype

    @abstractmethod
    def get_real_equivalent(self):
        """
        :return: Gets a real-valued COPY of the Complex Layer.
        """
        pass

    @abstractmethod
    def get_description(self) -> str:
        """
        :return: a string containing all the information of the layer
        """
        pass

    def _save_tensorboard_output(self, x, summary, step):
        x = self.call(x)
        with summary.as_default():
            if x.dtype == tf.complex64 or x.dtype == tf.complex128:
                tf.summary.histogram(name="Activation_value_" + str(self.layer_number) + "_real",
                                     data=tf.math.real(x), step=step)
                tf.summary.histogram(name="Activation_value_" + str(self.layer_number) + "_imag",
                                     data=tf.math.imag(x), step=step)
            elif x.dtype == tf.float32 or x.dtype == tf.float64:
                tf.summary.histogram(name="Activation_value_" + str(self.layer_number),
                                     data=x, step=step)
            else:
                logger.error("Input_dtype not supported. Should never have gotten here!", exc_info=True)
                sys.exit(-1)
        return x

    def save_tensorboard_checkpoint(self, x, weight_summary, activation_summary, step=None):
        self._save_tensorboard_weight(weight_summary, step)
        return self._save_tensorboard_output(x, activation_summary, step)

    @abstractmethod
    def _save_tensorboard_weight(self, weight_summary, step):
        pass

    @abstractmethod
    def trainable_variables(self):
        pass

    @abstractmethod
    def call(self, inputs):
        pass

    def get_output_shape_description(self) -> str:
        # output_string = ""
        if isinstance(self.output_size, Iterable):
            output_string = "(None, " + ", ".join([str(x) for x in self.output_size]) + ")"
        else:
            output_string = "(None, " + str(self.output_size) + ")"
        return output_string


class Flatten(ComplexLayer):

    def __init__(self, input_size=None, input_dtype=None):
        # Win x2: giving None as input_size will also make sure Flatten is not the first layer
        super().__init__(input_size=input_size, output_size=self._get_output_size, input_dtype=input_dtype)

    def __deepcopy__(self, memodict=None):
        return Flatten()

    def _get_output_size(self):
        self.output_size = np.prod(self.input_size)

    def get_real_equivalent(self):
        return self.__deepcopy__()

    def get_description(self) -> str:
        return "Complex Flatten"

    def _save_tensorboard_weight(self, weight_summary, step):
        return None

    def call(self, inputs):
        return tf.reshape(inputs, (inputs.shape[0], self.output_size))

    def trainable_variables(self):
        return []


class ComplexDense(ComplexLayer):
    """
    Fully connected complex-valued layer
    Implements the operation:
        activation(dot(input, weights) + bias)
    - where data types can be either complex or real.
    - activation is the element-wise activation function passed as the activation argument,
    - weights is a matrix created by the layer
    - bias is a bias vector created by the layer
    """

    def __init__(self, output_size, input_size=None, 
                 activation: Optional[t_activation] = None, input_dtype: Optional[t_Dtype] = None,
                 weight_initializer: Optional[RandomInitializer] = None,
                 bias_initializer: Optional[RandomInitializer] = None,
                 dropout: Optional[float] = None):
        """
        Initializer of the Dense layer
        :param output_size: Output size of the layer
        :param input_size: Input size of the layer
        :param activation: Activation function to be used.
            Can be either the function from cvnn.activation or tensorflow.python.keras.activations
            or a string as listed in act_dispatcher
        :param input_dtype: data type of the input. Default: np.complex64
            Supported data types:
                - np.complex64
                - np.float32
        :param weight_initializer: Initializer for the weights.
            Default: cvnn.initializers.GlorotUniform
        :param bias_initializer: Initializer fot the bias.
            Default: cvnn.initializers.Zeros
        :param dropout: Either None (default) and no dropout will be applied or a scalar
            that will be the probability that each element is dropped.
            Example: setting rate=0.1 would drop 10% of input elements.
        """
        super(ComplexDense, self).__init__(output_size=output_size, input_size=input_size, input_dtype=input_dtype)
        if activation is None:
            activation = 'linear'
        self.activation = activation
        # Test if the activation function changes datatype or not...
        self.__class__.__bases__[0].last_layer_output_dtype = \
            apply_activation(self.activation,
                             tf.cast(tf.complex([[1., 1.], [1., 1.]], [[1., 1.], [1., 1.]]), self.input_dtype)
                             ).numpy().dtype
        self.dropout = dropout  # TODO: I don't find the verification that it is between 0 and 1. I think I omitted it
        if weight_initializer is None:
            weight_initializer = initializers.GlorotUniform()
        self.weight_initializer = weight_initializer
        if bias_initializer is None:
            bias_initializer = initializers.Zeros()
        self.bias_initializer = bias_initializer
        self.w = None
        self.b = None
        self._init_weights()

    def __deepcopy__(self, memodict=None):
        if memodict is None:
            memodict = {}
        return ComplexDense(output_size=self.output_size, input_size=self.input_size,
                            activation=self.activation,
                            input_dtype=self.input_dtype,
                            weight_initializer=self.weight_initializer,
                            bias_initializer=self.bias_initializer, dropout=self.dropout
                            )

    def get_real_equivalent(self, output_multiplier=2, input_multiplier=2):
        """
        :param output_multiplier: Multiplier of output and input size (normally by 2)
        :return: real-valued copy of self
        """
        return ComplexDense(output_size=int(round(self.output_size * output_multiplier)),
                            input_size=int(round(self.input_size * input_multiplier)),
                            activation=self.activation, input_dtype=np.float32,
                            weight_initializer=self.weight_initializer,
                            bias_initializer=self.bias_initializer, dropout=self.dropout
                            )

    def _init_weights(self):
        self.w = tf.Variable(self.weight_initializer(shape=(self.input_size, self.output_size), dtype=self.input_dtype),
                             name="weights" + str(self.layer_number))
        self.b = tf.Variable(self.bias_initializer(shape=self.output_size, dtype=self.input_dtype),
                             name="bias" + str(self.layer_number))

    def get_description(self):
        fun_name = get_func_name(self.activation)
        out_str = "Dense layer:\n\tinput size = " + str(self.input_size) + "(" + str(self.input_dtype) + \
                  ") -> output size = " + str(self.output_size) + \
                  ";\n\tact_fun = " + fun_name + ";\n\tweight init = " \
                                                 "\n\tDropout: " + str(self.dropout) + "\n"
        # + self.weight_initializer.__name__ + "; bias init = " + self.bias_initializer.__name__ + \
        return out_str

    def call(self, inputs):
        """
        Applies the layer to an input
        :param inputs: input
        :param kwargs:
        :return: result of applying the layer to the inputs
        """
        # TODO: treat bias as a weight. It might optimize training (no add operation, only mult)
        with tf.name_scope("ComplexDense_" + str(self.layer_number)) as scope:
            if tf.dtypes.as_dtype(inputs.dtype) is not tf.dtypes.as_dtype(np.dtype(self.input_dtype)):
                logger.warning("Dense::apply_layer: Input dtype " + str(inputs.dtype) + " is not as expected ("
                               + str(tf.dtypes.as_dtype(np.dtype(self.input_dtype))) +
                               "). Casting input but you most likely have a bug")
            out = tf.add(tf.matmul(tf.cast(inputs, self.input_dtype), self.w), self.b)
            y_out = apply_activation(self.activation, out)

            if self.dropout:
                # $ tf.nn.dropout(tf.complex(x,x), rate=0.5)
                # *** ValueError: x has to be a floating point tensor since it's going to be scaled.
                # Got a <dtype: 'complex64'> tensor instead.
                drop_filter = tf.nn.dropout(tf.ones(y_out.shape), rate=self.dropout)
                y_out_real = tf.multiply(drop_filter, tf.math.real(y_out))
                y_out_imag = tf.multiply(drop_filter, tf.math.imag(y_out))
                y_out = tf.cast(tf.complex(y_out_real, y_out_imag), dtype=y_out.dtype)
            return y_out

    def _save_tensorboard_weight(self, summary, step):
        with summary.as_default():
            if self.input_dtype == np.complex64 or self.input_dtype == np.complex128:
                tf.summary.histogram(name="ComplexDense_" + str(self.layer_number) + "_w_real",
                                     data=tf.math.real(self.w), step=step)
                tf.summary.histogram(name="ComplexDense_" + str(self.layer_number) + "_w_imag",
                                     data=tf.math.imag(self.w), step=step)
                tf.summary.histogram(name="ComplexDense_" + str(self.layer_number) + "_b_real",
                                     data=tf.math.real(self.b), step=step)
                tf.summary.histogram(name="ComplexDense_" + str(self.layer_number) + "_b_imag",
                                     data=tf.math.imag(self.b), step=step)
            elif self.input_dtype == np.float32 or self.input_dtype == np.float64:
                tf.summary.histogram(name="ComplexDense_" + str(self.layer_number) + "_w",
                                     data=self.w, step=step)
                tf.summary.histogram(name="ComplexDense_" + str(self.layer_number) + "_b",
                                     data=self.b, step=step)
            else:
                # This case should never happen. The constructor should already have checked this
                logger.error("Input_dtype not supported.", exc_info=True)
                sys.exit(-1)

    def trainable_variables(self):
        return [self.w, self.b]


class Dropout(ComplexLayer):

    def __init__(self, rate, noise_shape=None, seed=None):
        """
        :param rate: A scalar Tensor with the same type as x.
            The probability that each element is dropped.
            For example, setting rate=0.1 would drop 10% of input elements.
        :param noise_shape: A 1-D Tensor of type int32, representing the shape for randomly generated keep/drop flags.
        :param seed:  A Python integer. Used to create random seeds. See tf.random.set_seed for behavior.
        """
        # tf.random.set_seed(seed)
        self.rate = rate
        self.noise_shape = noise_shape
        self.seed = seed
        # Win x2: giving None as input_size will also make sure Dropout is not the first layer
        super().__init__(input_size=None, output_size=self.dummy, input_dtype=None)

    def dummy(self):
        self.output_size = self.input_size

    def call(self, inputs):
        drop_filter = tf.nn.dropout(tf.ones(inputs.shape), rate=self.rate, noise_shape=self.noise_shape, seed=self.seed)
        y_out_real = tf.multiply(drop_filter, tf.math.real(inputs))
        y_out_imag = tf.multiply(drop_filter, tf.math.imag(inputs))
        return tf.cast(tf.complex(y_out_real, y_out_imag), dtype=inputs.dtype)

    def _save_tensorboard_weight(self, weight_summary, step):
        # No tensorboard things to save
        return None

    def get_description(self):
        return "Complex Dropout:\n\trate={}".format(self.rate)

    def __deepcopy__(self, memodict=None):
        if memodict is None:
            memodict = {}
        return Dropout(rate=self.rate, noise_shape=self.noise_shape, seed=self.seed)

    def get_real_equivalent(self):
        return self.__deepcopy__()  # Dropout layer is dtype agnostic

    def trainable_variables(self):
        return []

class ComplexConvolution(ComplexLayer):

    def __init__(self, filter_shape: t_kernel_shape, input_shape: Optional[t_input_shape] = None,
                 padding: t_padding_shape = "valid", data_format: str = 'channels_last',
                 strides: t_stride_shape = 1, input_dtype: Optional[t_Dtype] = None,
                 dimensions: int = 2):
        self.dimensions = int(dimensions)
        self.data_format = data_format.lower()
        assert self.data_format in DATA_FORMAT, f"data_format = {self.data_format} unknown"
        if input_shape is None:
            self.input_size = None
        elif isinstance(input_shape, (tuple, list)):
            self.input_size = tuple(input_shape)
        else:
            logger.error(f"Input shape: {input_shape} format not supported. It must be an int or a tuple")
            sys.exit(-1)
        super(ComplexConvolution, self).__init__(self._calculate_shapes, self.input_size, input_dtype,
                                                 filter_shape, padding, strides)

    @abstractmethod
    def _verifiy_and_create_filter(self, filter_shape: t_kernel_shape, dimensions: int = 2):
        pass

    @abstractmethod
    def _calculate_output_shape(self):
        pass

    def _verify_and_create_padding(self, padding: t_padding_shape, dimensions: int = 2) -> None:
        """
        Creates self.padding variable and verifies it
        :param dimensions: (default 2), total dimensions to be padded (ex. for conv2D is 2, for conv1D is 1).
        :return: None
        """
        # Get padding
        if isinstance(padding, int):
            self.padding = [[padding, padding]] * dimensions
        elif isinstance(padding, (tuple, list)):
            self.padding = list(padding)
            if len(self.padding) in {dimensions + 2, dimensions}:  # TODO: I have to check each element is a list
                logger.error("Padding should have length 2 or 4")
                exit(-1)
        elif isinstance(padding, str):
            padding = padding.lower()
            if padding in PADDING_MODES:
                if padding == "valid":
                    self.padding = [[0, 0]] * (len(self.input_size) + 1)
                elif padding == "same":
                    pads = [np.floor(k / 2) for k in self.filter_shape[:2]]
                    self.padding = [[p, p] for p in pads]
                elif padding == "full":
                    pads = [k - 1 for k in self.filter_shape[:2]]
                    self.padding = [[p, p] for p in pads]
                else:
                    logger.error(f"Unknown padding {self.padding} but listed in PADDING_MODES!")
                    sys.exit(-1)
            else:
                logger.error(f"Unknown padding {padding}")
                sys.exit(-1)
        else:
            logger.error(f"Padding: {padding} format not supported. It must be an int or a tuple")
            sys.exit(-1)
        # Fill missing data
        if len(self.padding) == 2:
            self.padding = [[0, 0]] + self.padding  # Don't pad images itself!
            if self.data_format == "channels_last":  # And don't pad the channels neither!
                self.padding.append([0, 0])
            elif self.data_format == "channels_first":
                self.padding = self.padding.insert(1, [0, 0])
            else:
                logger.error(f"Unknown data_format {self.data_format}")
                sys.exit(-1)
        # Verify padding was done correctly
        assert len(self.padding) == dimensions + 2, f"padding = {self.padding} should be of size {dimensions + 2}"
        assert self.padding[0] == [0, 0], "First element of the padding should be [0, 0] not to pad images!"
        if (self.data_format == "channels_first" and self.padding[1] != [0, 0]) or \
                (self.data_format == "channels_last" and self.padding[-1] != [0, 0]):
            logger.error("Channels should have no padding!")
            sys.exit(-1)

    def _verify_and_create_stride(self, stride: t_stride_shape, dimensions: int = 2) -> None:
        """
        Creates self.stride variable and verifies it
        :param dimensions: (default 2), total dimensions to be padded (ex. for conv2D is 2, for conv1D is 1).
        :return: None
        """
        if isinstance(stride, (tuple, list)):
            self.stride = list(stride)
            set_trace()
            assert len(self.stride) in {1, dimensions, dimensions + 2}
        elif isinstance(stride, int):
            # TODO: This is probably unnecessary actually...
            self.stride = [stride]
        else:
            logger.error(f"stride: {stride} format not supported. It must be either int or list of ints that "
                         f"has length 1, {dimensions} or {dimensions + 2}.")
            sys.exit(-1)
        # Here I have self stride of sizes {1, dimensions, dimensions + 2} but I want dimensions + 2 (compulsory)
        if len(self.stride) == 1:  # Move size 1 to size dimensions
            self.stride = self.stride * dimensions
        if len(self.stride) != dimensions + 2:  # Move size dimensions to dimensions +2
            self.stride.insert(0, 1)  # Image stride is one
            if self.data_format == "channels_last":  # Channel stride = 1
                self.stride.append(1)
            elif self.data_format == "channels_first":
                self.stride.insert(1, 1)
            print(self.stride)

    def _calculate_shapes(self, filter_shape: t_kernel_shape, padding: t_padding_shape, stride: t_stride_shape):
        """
        Sets the values of filter_shape, padding, stride and output_size
        """
        self._verify_input_size()
        self._verifiy_and_create_filter(filter_shape)
        self._verify_and_create_padding(padding)
        self._verify_and_create_stride(stride)

        return self._calculate_output_shape()

    def _verify_input_size(self, dimension=2):
        if isinstance(self.input_size, int):
            self.input_size = (self.input_size,) * dimension
        if len(self.input_size) != dimension + 1:
            if len(self.input_size) == dimension:  # Assume channels where omitted (so they are added)
                if self.data_format == "channels_last":  # And don't pad the channels neither!
                    self.input_size = self.input_size + (1,)
                elif self.data_format == "channels_first":
                    self.input_size = (1,) + self.input_size
            else:
                form = "(channels, row, cols)" if self.data_format == "channels_first" else "(row, cols, channels)"
                logger.error(
                    f"input_size should be rank {dimension + 1} of the form {form} but received {self.input_size}")
                sys.exit(-1)
    


class ComplexConv2D(ComplexConvolution):
    # http://datahacker.rs/convolution-rgb-image/   For RGB images
    # https://towardsdatascience.com/a-beginners-guide-to-convolutional-neural-networks-cnns-14649dbddce8

    def __init__(self, filters: int, kernel_size: t_kernel_2_shape,
                 input_shape: Optional[t_input_shape] = None,
                 padding: t_padding_shape = "valid", data_format: str = 'channels_last', dilatation_rate=(1, 1),
                 strides: t_kernel_2_shape = 1,
                 activation: Optional[t_activation] = None, dropout: Optional[float] = None,
                 weight_initializer: Optional[RandomInitializer] = initializers.GlorotUniform(),
                 bias_initializer: Optional[RandomInitializer] = initializers.Zeros(),
                 input_dtype: Optional[t_Dtype] = None
                 ):
        """
        :param filters: Integer, the dimensionality of the output space
            (i.e. the number of output filters in the convolution).
        :param kernel_size: An integer or tuple/list of 2 or 4 integers,
            specifying the height and width of the 2D convolution window.
            Can be a single integer to specify the same value for all spatial dimensions.
        :param input_shape: An integer or tuple/list of integers of the input tensor shape.
        :param padding: One of the following:
            - Integer: zero's to be added at the bottom, top, right and left of the image (same for every dimension)
            - List or Tuple of the form: 
                - [[pad_top, pad_bottom], [pad_left, pad_right]]
                - [[0, 0], [pad_top, pad_bottom], [pad_left, pad_right], [0, 0]] for data_format = channels_last (default)
                - [[0, 0], [0, 0], [pad_top, pad_bottom], [pad_left, pad_right]] for data_format = channels_first
            - string (case in-sensitive):
                - "same":  output size is the same as input size (for an odd kernel size)
                - "valid": no padding applied
                - "full":  output size bigger than input size (for unit stride)
        :param data_format: A string, one of channels_last (default) or channels_first.
            The ordering of the dimensions in the inputs.
            channels_last corresponds to inputs with shape (batch_size, ..., channels) while channels_first corresponds
            to inputs with shape (batch_size, channels, ...)
        :param dilatation_rate: An int or list of ints that has length 1, 2 or 4, defaults to 1. 
            The dilation factor for each dimension of input. 
            If a single value is given it is replicated in the H and W dimension. 
            By default the N and C dimensions are set to 1. If set to k > 1, 
            there will be k-1 skipped cells between each filter element on that dimension. 
            The dimension order is determined by the value of data_format, see above for details. 
            Dilations in the batch and depth dimensions if a 4-d tensor must be 1.
        :param activation: Activation function to be used.
            Can be either the function from cvnn.activation or tensorflow.python.keras.activations
            or a string as listed in act_dispatcher
        :param input_dtype: data type of the input. Default: np.complex64
            Supported data types:
                - np.complex64
                - np.float32
        :param weight_initializer: Initializer for the weights.
            Default: cvnn.initializers.GlorotUniform
        :param bias_initializer: Initializer fot the bias.
            Default: cvnn.initializers.Zeros
        :param dropout: Either None (default) and no dropout will be applied or a scalar
            that will be the probability that each element is dropped.
            Example: setting rate=0.1 would drop 10% of input elements.
        """
        self.filters = filters
        self.dropout = dropout
        self.dilatation = dilatation_rate
        if activation is None:
            activation = 'linear'
        self.activation = activation
        if weight_initializer is None:
            weight_initializer = initializers.GlorotUniform()
        self.weight_initializer = weight_initializer
        if bias_initializer is None:
            bias_initializer = initializers.Zeros()
        self.bias_initializer = bias_initializer
        super(ComplexConv2D, self).__init__(filter_shape=kernel_size, input_shape=input_shape, padding=padding, 
                                            data_format=data_format, strides=strides, input_dtype=input_dtype,
                                            )
        # Test if the activation function changes datatype or not...
        self.__class__.__bases__[0].last_layer_output_dtype = \
            np.dtype(apply_activation(self.activation,
                                      tf.cast(tf.complex([[1., 1.], [1., 1.]], [[1., 1.], [1., 1.]]), self.input_dtype)
                                      ).numpy().dtype)
        self.kernels = tf.Variable(self.weight_initializer(shape=self.kernel_size, dtype=self.input_dtype),
                                   name="kernel" + str(self.layer_number))
        self.bias = tf.Variable(self.bias_initializer(shape=self.output_size, dtype=self.input_dtype),
                                name="bias" + str(self.layer_number))

    def _verifiy_and_create_filter(self, filter_shape: t_kernel_shape, dimensions: int = 2):
        """
        Verifies Kernel shape and creates self.filter_shape variable
        :param dimensions: (default 2), total dimensions to be padded (ex. for conv2D is 2, for conv1D is 1).
        :return: None
        """
        channels = self.input_size[0] if self.data_format == "channels_first" else self.input_size[-1]
        if isinstance(filter_shape, int):
            self.kernel_size = [filter_shape] * dimensions + [channels] + [self.filters]
        elif isinstance(filter_shape, (tuple, list)):
            self.kernel_size = list(filter_shape)
            if len(self.kernel_size) == dimensions:
                # Assume only height and width was given, in and out channels are calculated automatically
                self.kernel_size = self.kernel_size + [channels] + [self.filters]
            elif len(self.kernel_size) == dimensions + 1:
                # Assume only height, width and in_channels was given, out channels is calculated automatically
                self.kernel_size = self.kernel_size + [self.filters]
        else:
            logger.error(f"Kernel shape: {filter_shape} format not supported. It must be an int or a tuple. "
                         f"Received {filter_shape}")
            sys.exit(-1)
        assert len(self.kernel_size) == dimensions + 2, f"Kernel must be a {dimensions + 2}-D tensor of shape " \
                                                         f"[filter_height, filter_width, in_channels, out_channels] " \
                                                         f"but it had size {len(self.kernel_size)}."
        if not np.all(np.asarray(self.kernel_size) >= 1):
            logger.error(f"Kernel shape must have all values bigger than 1: {self.kernel_size}.")
            sys.exit(-1)
        if not np.all(np.asarray(self.kernel_size) == np.asarray(self.kernel_size).astype(int)):
            logger.error(f"Kernel shape must have all integer values: {self.kernel_size}.")
            sys.exit(-1)
    
    def _verify_inputs(self, inputs):
        inputs = tf.convert_to_tensor(inputs)  # This checks all images are same size! Nice
        if inputs.dtype != self.input_dtype:
            logger.warning("input dtype (" + str(inputs.dtype) + ") not what expected ("
                           + str(self.input_dtype) + "). Attempting cast...")
            inputs = tf.dtypes.cast(inputs, self.input_dtype)
        if len(inputs.shape) == len(self.input_size) - 1:  # Assume only one image and channels omitted
            inputs = tf.reshape(inputs, (1,) + inputs.shape)
        if len(inputs.shape) == len(self.input_size):  # Assume channels omitted
            if self.data_format == "channels_last":
                inputs = tf.reshape(inputs, inputs.shape + (1,))
            elif self.data_format == "channels_first":
                inputs = tf.reshape(inputs, (inputs.shape[0],) + (1,) + inputs.shape[1:])
            else:
                logger.error(f"Unknown data_format {self.data_format}")
                sys.exit(-1)
        if inputs.shape[1:] != self.input_size:
            logger.error(f"Expected shape {self.input_size}. Got {inputs.shape}")
            sys.exit(-1)
        return inputs

    def _calculate_output_shape(self):
        out_list = []
        indx = 1 if self.data_format == "channels_last" else 2
        for i, p, k, s in zip(self.input_size[indx - 1:], self.padding[indx:], self.kernel_size[:2],
                              self.stride[indx:]):
            # self.kernel_size will be size 2 and will truncate the zip.
            # 2.4 on https://arxiv.org/abs/1603.07285
            out_list.append(int(np.floor((i + 2 * p[0] - k) / s) + 1))
        if self.data_format == "channels_last":  # New channels are actually the filters
            out_list.append(self.filters)
        elif self.data_format == "channels_first":
            out_list.insert(0, self.filters)
        self.output_size = tuple(out_list)
        assert len(self.output_size) == 3, f"Error! output shape should have been 3 but was " \
                                           f"{len(self.output_size)} : {self.output_size}"
        return self.output_size

    def call(self, inputs):
        """
        :param inputs:
        :return:
        """
        inputs = self._verify_inputs(inputs=inputs)
        data_format = "NHWC" if self.data_format == "channels_last" else "NCHW"
        inputs_r = tf.math.real(inputs)
        inputs_i = tf.math.imag(inputs)
        kernel_r = tf.math.real(self.kernels)
        kernel_i = tf.math.imag(self.kernels)
        real_res = tf.nn.conv2d(inputs_r, kernel_r,
                                strides=self.stride, padding=self.padding,
                                data_format=data_format, dilations=self.dilatation
                                ) - tf.nn.conv2d(inputs_i, kernel_i,
                                                 strides=self.stride, padding=self.padding,
                                                 data_format=data_format, dilations=self.dilatation
                                                 )
        imag_res = tf.nn.conv2d(inputs_r, kernel_i,
                                strides=self.stride, padding=self.padding,
                                data_format=data_format, dilations=self.dilatation
                                ) + tf.nn.conv2d(inputs_i, kernel_r,
                                                 strides=self.stride, padding=self.padding,
                                                 data_format=data_format, dilations=self.dilatation
                                                 )
        if self.input_dtype in COMPLEX:
            res = tf.complex(real_res, imag_res) + self.bias
        elif self.input_dtype in REAL:
            res = real_res + self.bias
        else:
            logger.error(f"self.input_dtype ({self.input_dtype}) not supported")
            sys.exit(-1)
        y_out = apply_activation(self.activation, res)
        if self.dropout:
            drop_filter = tf.nn.dropout(tf.ones(y_out.shape), rate=self.dropout)
            y_out_real = tf.multiply(drop_filter, tf.math.real(y_out))
            y_out_imag = tf.multiply(drop_filter, tf.math.imag(y_out))
            y_out = tf.cast(tf.complex(y_out_real, y_out_imag), dtype=y_out.dtype)
        return y_out

    def apply_padding(self, inputs):
        pad = [[0, 0]]  # No padding to the images itself
        for p in self.padding_shape:
            pad.append([p, p])  # This method add same pad to beginning and end
        pad.append([0, 0])  # No padding to the channel
        return tf.pad(inputs, tf.constant(pad), "CONSTANT", 0)

    def _save_tensorboard_weight(self, weight_summary, step):
        return None  # TODO

    def get_description(self):
        fun_name = get_func_name(self.activation)
        out_str = "Complex Convolutional layer:\n\tinput size = " + str(self.input_size) + \
                  "(" + str(self.input_dtype) + \
                  ") -> output size = " + str(self.output_size) + \
                  "\n\tkernel shape = (" + "x".join([str(x) for x in self.filter_shape]) + ")" + \
                  "\n\tstride shape = (" + "x".join([str(x) for x in self.stride]) + ")" + \
                  "\n\tzero padding shape = (" + "x".join([str(x) for x in self.padding]) + ")" + \
                  ";\n\tact_fun = " + fun_name + ";\n\tweight init = " \
                  + self.weight_initializer.__name__ + "; bias init = " + self.bias_initializer.__name__ + "\n"
        return out_str

    def __deepcopy__(self, memodict=None):
        if memodict is None:
            memodict = {}
        return ComplexConv2D(filters=self.filters, kernel_size=self.filter_shape, input_shape=self.input_size,
                             padding=self.padding_shape, strides=self.stride_shape, input_dtype=self.input_dtype)

    def get_real_equivalent(self):
        return ComplexConv2D(filters=self.filters, kernel_size=self.filter_shape, input_shape=self.input_size,
                             padding=self.padding_shape, strides=self.stride_shape, input_dtype=np.float32)

    def trainable_variables(self):
        return [self.kernels, self.bias]


if __name__ == "__main__":
    img2 = np.array([
        [10, 10, 10, 0, 0, 0],
        [10, 10, 10, 0, 0, 0],
        [10, 10, 10, 0, 0, 0],
        [10, 10, 10, 0, 0, 0],
        [10, 10, 10, 0, 0, 0],
        [10, 10, 10, 0, 0, 0]
    ]).astype(np.float32)

    conv = ComplexConv2D(filters=5, kernel_size=3, input_shape=img2.shape, input_dtype=np.float32)
    print(conv.call(img2))


t_layers_shape = Union[ndarray, List[ComplexLayer], Set[ComplexLayer]]

__author__ = 'J. Agustin BARRACHINA'
__copyright__ = 'Copyright 2020, {project_name}'
__credits__ = ['{credit_list}']
__license__ = '{license}'
__version__ = '0.0.29'
__maintainer__ = 'J. Agustin BARRACHINA'
__email__ = 'joseagustin.barra@gmail.com; jose-agustin.barrachina@centralesupelec.fr'
__status__ = '{dev_status}'
