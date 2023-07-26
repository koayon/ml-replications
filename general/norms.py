from typing import Optional, Tuple, Union

import numpy as np
import torch as t
from einops import rearrange, repeat
from torch import nn


class BatchNorm2d(nn.Module):
    running_mean: t.Tensor
    "running_mean: shape (num_features,)"
    running_var: t.Tensor
    "running_var: shape (num_features,)"

    def __init__(self, num_features: int, eps=1e-05, momentum=0.1):
        """Like nn.BatchNorm2d with affine=True."""

        super().__init__()

        self.eps = eps
        self.num_features = num_features
        self.momentum = momentum

        # By default the affine transform is the identity (might learn something else tweaked slightly)
        self.weight = nn.Parameter(t.ones(num_features))  # channels
        self.bias = nn.Parameter(t.zeros(num_features))  # channels

        # Buffers are variables that are part of the model but not trainable parameters.
        # They aren't learned.
        # Each channel (red, blue, green) has its own mean and variance.
        self.register_buffer("running_mean", t.zeros(num_features))  # channels
        self.register_buffer("running_var", t.ones(num_features))  # channels

        self.register_buffer("num_batches_tracked", t.tensor(0))  # scalar

    def forward(self, x: t.Tensor) -> t.Tensor:
        """Normalises each channel along the batch.
        To be used at the minibatch level.
        Downside is that it requires large-ish mini-batches to be useful but large batches may require too much memory.
        Generally prefer LayerNorm or RMSNorm

        x: shape (batch, channels, height, width)
        Return: shape (batch, channels, height, width)
        """
        _batch, channels, _height, _width = x.shape
        assert channels == self.num_features

        # If training we're going to get the mean, var from our current batch
        if self.training:
            mean = t.mean(
                x, dim=(0, 2, 3)
            )  # average over batch and spatial dimensions shape(channels)
            var = t.var(
                x, dim=(0, 2, 3), unbiased=False
            )  # variance of batch and spatial dimensions shape(channels)

            # Update running mean and var
            self.running_mean = (
                1 - self.momentum
            ) * self.running_mean + self.momentum * mean
            self.running_var = (
                1 - self.momentum
            ) * self.running_var + self.momentum * var

        # For inference grab the running_mean/var from the training data and use this instead
        else:
            mean = self.running_mean
            var = self.running_var

        # Rearrange shape(channels) tensors to broadcasts well
        # Takes (channels) -> (1, channels, 1, 1)
        broadcast = lambda v: v.reshape(1, self.num_features, 1, 1)

        # Normalise then learned affine transform
        x_norm = (x - broadcast(mean)) / (broadcast(t.sqrt(var)) + self.eps)
        x_norm *= broadcast(self.weight)
        x_norm += broadcast(self.bias)

        return x_norm

    def extra_repr(self) -> str:
        return f"BatchNorm2d - eps: {self.eps}, momentum: {self.momentum}, num_features: {self.num_features}"
