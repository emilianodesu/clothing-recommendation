import torch
from hm_recommender.model.two_tower import TwoTower


def test_train_save_load_preserves_scores(tmp_path):
    torch.manual_seed(42)
    model = TwoTower(5, [8, 4, 3, 3])
    users = torch.tensor([1, 2]); history = torch.ones((2, 4))
    items = torch.tensor([[2, 2, 2, 2], [3, 3, 2, 2]])
    scores = (model.encode_user(users, history) * model.encode_item(items)).sum(1)
    assert model.encode_item(items).shape == (2, 64)
    path = tmp_path / 'model.pt'
    torch.save(model.state_dict(), path)
    clone = TwoTower(5, [8, 4, 3, 3])
    clone.load_state_dict(torch.load(path, weights_only=True))
    torch.testing.assert_close(scores, (clone.encode_user(users, history) * clone.encode_item(items)).sum(1), atol=1e-6, rtol=0)
