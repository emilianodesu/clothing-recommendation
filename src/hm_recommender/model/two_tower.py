import torch
from torch import nn
from torch.nn import functional as F


class TwoTower(nn.Module):
    def __init__(self, users, item_sizes):
        super().__init__()
        self.settings = {'users': users, 'item_sizes': list(item_sizes)}
        self.user_id = nn.Embedding(users, 64, padding_idx=0, sparse=True)
        self.items = nn.ModuleList(nn.Embedding(size, dim, padding_idx=0, sparse=True)
                                   for size, dim in zip(item_sizes, [64, 8, 8, 8], strict=True))
        self.user_mlp = nn.Sequential(nn.Linear(68, 64), nn.ReLU(), nn.Linear(64, 64))
        self.item_mlp = nn.Sequential(nn.Linear(88, 64), nn.ReLU(), nn.Linear(64, 64))

    def encode_user(self, ids, history):
        return F.normalize(self.user_mlp(torch.cat([self.user_id(ids), history], dim=-1)), dim=-1)

    def encode_item(self, attributes):
        return F.normalize(self.item_mlp(torch.cat([layer(attributes[..., i]) for i, layer in enumerate(self.items)], dim=-1)), dim=-1)
