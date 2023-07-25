import time
from typing import Tuple

import deepspeed
import tiktoken
import torch as t
import torch.nn as nn
from einops import rearrange, repeat
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset, RandomSampler, SequentialSampler
from tqdm import tqdm

from switch_transformer.model import SparseMoETransformer

device = "cuda" if t.cuda.is_available() else "cpu"


def get_shakespeare_data() -> Tuple[t.Tensor, t.Tensor]:
    """Get the Shakespeare dataset."""
    data_source = "data/tiny_shakespeare.txt"
    # Get text from file and convert to tensors for training
    with open(data_source, "r") as f:
        text = f.read()
    tokeniser = tiktoken.encoding_for_model("gpt2")
    tokenised_text = tokeniser.encode(text)  # list of ints
    train_split = int(len(tokenised_text) * 0.9)
    full_data = t.tensor(tokenised_text, dtype=t.long, device=device)  # len_of_text

    train_data = full_data[:train_split]
    test_data = full_data[train_split:]
    # print(f"{train_data.shape=}")
    # print(f"{test_data.shape=}")
    return train_data, test_data  # vectors of ints


class ShakespeareDataset(Dataset):
    """Train Dataset for Shakespeare data."""

    def __init__(self, data: t.Tensor, block_size: int):
        data.to(device)
        self.data = data
        self.block_size = block_size

    def __len__(self):
        return self.data.shape[0] // self.block_size

    def __getitem__(self, idx):
        return self.data[idx * self.block_size : (idx + 1) * self.block_size]


def evaluate(model: nn.Module, test_dataloader: DataLoader) -> float:
    """Evaluate the model on the test set."""
    # print(f"{len(test_dataloader)}")
    with t.inference_mode():
        total_loss = 0
        for _batch_num, batch_data in enumerate(test_dataloader):
            # batch_data  # batch, seq_len
            # print(f"{batch_data.shape=}")

            target_tokens = batch_data[:, 1:]  # batch, seq_len - 1

            logits, _cache = model(batch_data)
            logits = logits[:, :-1, :]  # batch, seq_len - 1, vocab_size

            flattened_logits = rearrange(logits, "b s v -> (b s) v")
            flattened_targets = rearrange(target_tokens, "b s -> (b s)")

            probs = t.softmax(logits, dim=-1)  # batch, seq_len - 1, vocab_size

            loss = F.cross_entropy(flattened_logits, flattened_targets)
            total_loss += loss.item()

        return total_loss / len(test_dataloader)


def train(model: nn.Module) -> nn.Module:
    """Train the model on the data source."""
    # Get dataset
    train_data, test_data = get_shakespeare_data()
    train_dataset = ShakespeareDataset(train_data, block_size=128)
    test_dataset = ShakespeareDataset(test_data, block_size=128)

    # Create dataloaders
    train_dataloader = DataLoader(
        train_dataset,
        sampler=RandomSampler(train_dataset, replacement=True),
        batch_size=8,
        shuffle=False,
        num_workers=6,
    )
    test_dataloader = DataLoader(
        test_dataset,
        sampler=RandomSampler(test_dataset, replacement=True),
        batch_size=8,
        shuffle=False,
        num_workers=6,
    )

    # Set up the optimiser
    optimiser = t.optim.Adam(model.parameters(), lr=0.001)

    model.to(device)

    # Train the model
    for epoch in range(1, 2):
        model.train()
        for batch_num, batch_data in enumerate(train_dataloader):
            # batch_data  # batch seq_len

            # print(f"{batch_data.shape=}")

            optimiser.zero_grad()

            target_tokens = batch_data[:, 1:]  # batch seq_len - 1
            logits, _cache = model(batch_data)
            logits = logits[:, :-1, :]  # batch seq_len - 1, vocab_size
            # print(f"{logits=}")
            # print(logits.shape)

            flattened_logits = rearrange(logits, "b s v -> (b s) v")  # bs, vocab_size
            flattened_targets = rearrange(target_tokens, "b s -> (b s)")  # bs

            # print(f"{probs.shape=}")
            # print(f"{targets.shape=}")

            loss = F.cross_entropy(flattened_logits, flattened_targets)
            # print(loss)
            loss.backward()
            optimiser.step()

            # if batch_num % 5 == 0:
            if True:
                test_loss = evaluate(model, test_dataloader)
                print(f"Epoch: {epoch}, Batch: {batch_num}, Test Loss: {test_loss}")
                # print(f"Epoch: {epoch}, Batch: {batch_num}, Test Loss: {loss}")

    return model


def main():
    # Set up the model
    model = SparseMoETransformer(
        hidden_size=512,
        num_layers=4,
    )
    model = model.to(device)

    # Train the model
    trained_model = train(model)

    # Save the model
    save_model(trained_model, "moe.pt")


def save_model(model, model_dest):
    """Save the model to the model_dest."""
    t.save(model.state_dict(), model_dest)
    print(f"Saved model to {model_dest}")


if __name__ == "__main__":
    main()

# TODO: Put all variables into a config.py file to import. Want to decrease hidden size etc. to make it run faster.
# TODO: Add deepspeed
# TODO: Make into class