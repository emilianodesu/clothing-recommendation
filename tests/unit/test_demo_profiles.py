"""Profile labels must be supported by purchases, not guessed demographics."""
import csv
import io
import struct
from types import SimpleNamespace
import zipfile


def catalogue():
    traits = {}
    for prefix, colour, group in [('d', 'Black', 'Menswear'), ('l', 'White', 'Ladieswear')]:
        for i, kind in enumerate(('T-shirt', 'Trousers', 'Sweater', 'Shirt', 'Hoodie', 'Jacket')):
            traits[prefix+str(i)] = dict(article_id=prefix+str(i), product_type_name=kind,
                colour_group_name=colour, index_group_name=group, graphical_appearance_name='Solid')
    return traits


def test_selection_uses_real_histories_and_explains_the_match():
    from hm_recommender.api.demo_profiles import select_profiles
    traits = catalogue()
    rows = [('dark-buyer', list(traits)[:6]), ('light-buyer', list(traits)[6:])]
    result = select_profiles(rows, traits, {'dark-buyer', 'light-buyer'})
    assert {p['customer_id'] for p in result} == {'dark-buyer', 'light-buyer'}
    dark = next(p for p in result if p['customer_id'] == 'dark-buyer')
    assert dark['key'] == 'mens-dark'
    assert dark['matched_count'] == 6 and dark['history_count'] == 6
    assert all(x['colour_group_name'] == 'Black' for x in dark['examples'])
    assert len({p['label'] for p in result}) == len(result)


def test_missing_gender_or_pattern_metadata_never_invents_them():
    from hm_recommender.api.demo_profiles import select_profiles
    traits = catalogue()
    for row in traits.values():
        row.pop('index_group_name'); row.pop('graphical_appearance_name')
    result = select_profiles([('dark', list(traits)[:6])], traits, {'dark'})
    assert len(result) == 1 and result[0]['key'] == 'dark'
    assert result[0]['matched_count'] == 6


def test_small_unknown_and_simulated_histories_do_not_become_personas():
    from hm_recommender.api.demo_profiles import select_profiles
    traits = catalogue()
    rows = [('demo-new', list(traits)[:6]), ('few', ['d0', 'd0', 'd0']),
            ('unknown', ['missing']*8), ('no-majority', ['d0', 'd1', 'l0', 'l1'])]
    assert select_profiles(rows, traits, {'few', 'unknown', 'no-majority'}) == []


def test_selection_is_deterministic_and_preserves_alternative_candidates():
    from hm_recommender.api.demo_profiles import select_profiles
    traits = catalogue()
    rows = [('z', list(traits)[:6]), ('a', list(traits)[:6])]
    forward = select_profiles(rows, traits, {'z', 'a'})
    reverse = select_profiles(reversed(rows), traits, {'z', 'a'})
    assert forward == reverse
    assert [(p['key'], p['customer_id']) for p in forward] == [('mens-dark', 'a'), ('dark', 'z')]


def test_subtle_profiles_require_actual_diversity_and_known_colours():
    from hm_recommender.api.demo_profiles import select_profiles
    types = ['T-shirt', 'Trousers', 'Sweater', 'Shirt', 'Jacket', 'Dress']
    colours = ['Blue', 'Pink', 'Green', 'Red', 'Orange', 'White']
    traits = {str(i): dict(article_id=str(i), product_type_name=k, colour_group_name=c)
              for i, (k, c) in enumerate(zip(types, colours))}
    result = select_profiles([('mixed', list(traits))], traits, {'mixed'})
    assert len(result) == 1 and result[0]['key'] == 'varied'
    missing_colours = {key: dict(value) for key, value in traits.items()}
    for key in ('3', '4', '5'): missing_colours[key].pop('colour_group_name')
    assert select_profiles([('partial', list(traits))], missing_colours, {'partial'}) == []
    for row in traits.values(): row['colour_group_name'] = 'Blue'
    result = select_profiles([('mono', list(traits))], traits, {'mono'})
    assert len(result) == 1 and result[0]['key'] == 'one-colour'
    for row in traits.values(): row.pop('colour_group_name')
    assert select_profiles([('missing', list(traits))], traits, {'missing'}) == []


def test_archive_enrichment_is_optional_and_only_uses_catalogue_members(tmp_path):
    from hm_recommender.api.demo_profiles import article_traits
    base = SimpleNamespace(catalog={'001'}, filter_index={
        'product_type_name': {'T-shirt': {'001'}}, 'colour_group_name': {'Black': {'001'}}})
    archive = tmp_path/'photos.zip'
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(['article_id', 'index_group_name', 'graphical_appearance_name'])
    writer.writerows([['001','Menswear','Stripe'], ['999','Ladieswear','Dot']])
    with zipfile.ZipFile(archive,'w') as z: z.writestr('articles.csv',text.getvalue())
    enriched = article_traits(base, archive)
    assert set(enriched) == {'001'}
    assert enriched['001']['index_group_name'] == 'Menswear'
    assert enriched['001']['graphical_appearance_name'] == 'Stripe'
    missing = article_traits(base, tmp_path/'missing.zip')
    assert missing['001']['colour_group_name'] == 'Black'
    assert 'index_group_name' not in missing['001']
    archive.write_bytes(b'invalid zip')
    assert article_traits(base, archive) == missing


def test_one_colour_requires_three_types_within_that_same_colour():
    from hm_recommender.api.demo_profiles import select_profiles
    traits = {str(i): dict(article_id=str(i), product_type_name=kind, colour_group_name=colour)
              for i, (kind, colour) in enumerate([
                  ('T-shirt','Blue'), ('T-shirt','Blue'), ('Trousers','Blue'),
                  ('Trousers','Blue'), ('Jacket','Red')])}
    assert select_profiles([('buyer', list(traits))], traits, {'buyer'}) == []


def test_unsupported_archive_compression_keeps_catalogue_only_styles(tmp_path):
    from hm_recommender.api.demo_profiles import article_traits
    archive = tmp_path/'unsupported.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('articles.csv', 'article_id,index_group_name,graphical_appearance_name\n001,Menswear,Stripe\n')
    content = bytearray(archive.read_bytes())
    struct.pack_into('<H', content, 8, 99)
    struct.pack_into('<H', content, content.index(b'PK\x01\x02')+10, 99)
    archive.write_bytes(content)
    base = SimpleNamespace(catalog={'001'}, filter_index={'colour_group_name': {'Black': {'001'}}})
    assert article_traits(base, archive) == {'001': {'article_id':'001', 'colour_group_name':'Black'}}
