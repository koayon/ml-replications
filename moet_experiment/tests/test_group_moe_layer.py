import pytest
import torch as t

from general import device
from moet_experiment.group_moe_layer import GroupMoELayer
from moet_experiment.moet_config import MoETConfig

config = MoETConfig()
# config.hidden_size = 8


@pytest.mark.parametrize("num_experts", [8])
@pytest.mark.parametrize("router_str", ["hash", "linear"])
@pytest.mark.parametrize("group_size", [1, 2])
@pytest.mark.parametrize("seq_len", [4])
@pytest.mark.parametrize("batch_size", [1, 4])
@pytest.mark.parametrize("c", [1.0, 1.5])
def test_group_moe_layer(
    num_experts: int,
    router_str: str,
    group_size: int,
    seq_len: int,
    batch_size: int,
    c: float,
    config: MoETConfig = MoETConfig(),
):
    moe_layer = GroupMoELayer(
        num_experts=num_experts,
        router_str=router_str,
        layer_id="layer1",
        group_size=group_size,
        c=c,
        config=config,
    )
    moe_layer.to(device)

    x = t.randn(
        (batch_size, seq_len, config.hidden_size),
        requires_grad=True,
        device = device
    )
    input = t.randint(0, 100, (batch_size, seq_len), device= device)

    # Check that forward pass works
    y, _cache = moe_layer(x, input)
    assert y.size(0) == x.size(0)
    assert y.size(1) == x.size(1)
    assert y.size(2) == x.size(2)

    # Check that gradients are propagated
    y.sum().backward()
    assert x.grad is not None
    assert x.grad.shape == x.shape
    assert x.grad.requires_grad is False


def test_group_moe_layer_exceptions(num_experts = 8):
    moe_layer = GroupMoELayer(
        num_experts=num_experts,
        router_str="hash",
        layer_id="layer1",
        group_size=2,
        c=1.0,
    )

    # Test no input tokens for hash router
    x = t.randn((1, 16, config.hidden_size))
    with pytest.raises(AssertionError):
        moe_layer(x)

    # Test k and c are both 0
    with pytest.raises(AssertionError):
        moe_layer = GroupMoELayer(
            num_experts=num_experts,
            router_str="hash",
            layer_id="layer1",
            c=0,
        )

    # Test invalid router
    with pytest.raises(AssertionError):
        moe_layer = GroupMoELayer(
            num_experts=4,
            router_str="invalid",
            layer_id="layer1",
            c=1.0,
        )
