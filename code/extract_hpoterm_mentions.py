#! /usr/bin/env python3

import fileinput
import random
import re

from nltk.stem.snowball import SnowballStemmer

from dstruct.Mention import Mention
from dstruct.Sentence import Sentence
from helper.easierlife import get_all_phrases_in_sentence, \
    get_dict_from_TSVline, TSVstring2list, no_op
from helper.dictionaries import load_dict

max_mention_length = 8  # This is somewhat arbitrary

NEG_PROB = 0.005  # Probability of generating a random negative mention

# Keyword that seems to appear with phenotypes
VAR_KWS = frozenset([
    "abnormality", "affect", "apoptosis", "association", "cancer", "carcinoma",
    "case", "cell", "chemotherapy", "clinic", "clinical", "chromosome",
    "cronic", "deletion", "detection", "diagnose", "diagnosis", "disease",
    "drug", "family", "gene", "genome", "genomic", "genotype", "give", "grade",
    "group", "history", "infection", "inflammatory", "injury", "mutation",
    "pathway", "phenotype", "polymorphism", "prevalence", "protein", "risk",
    "severe", "stage", "symptom", "syndrome", "therapy", "therapeutic",
    "treat", "treatment", "variant" "viruses", "virus"])

PATIENT_KWS = frozenset(
    ["boy", "girl", "man", "woman", "men", "women", "patient", "patients"])

KEYWORDS = VAR_KWS | PATIENT_KWS

# Load the dictionaries that we need
english_dict = load_dict("english")
stopwords_dict = load_dict("stopwords")
inverted_hpoterms = load_dict("hpoterms_inverted")
hponames_to_ids = load_dict("hponames_to_ids")
genes_with_hpoterm = load_dict("genes_with_hpoterm")
# hpodag = load_dict("hpoparents")


stems = set()
for hpo_name in inverted_hpoterms:
    stem_set = inverted_hpoterms[hpo_name]
    stems |= stem_set
stems = frozenset(stems)

# The keys of the following dictionary are sets of stems, and the values are
# sets of hpoterms whose name, without stopwords, gives origin to the
# corresponding set of stems (as key)
hpoterms_dict = load_dict("hpoterms")

# Initialize the stemmer
stemmer = SnowballStemmer("english")


# Perform the supervision
def supervise(mentions, sentence):
    for mention in mentions:
        # Skip if we already supervised it (e.g., random mentions or
        # gene long names)
        if mention.is_correct is not None:
            continue
        # The next word is 'gene' or 'protein', so it's actually a gene
        if mention.words[-1].in_sent_idx < len(sentence.words) - 1:
            next_word = sentence.words[mention.words[-1].in_sent_idx + 1].word
            if next_word.casefold() in ["gene", "protein"]:
                mention.is_correct = False
                mention.type = "HPOTERM_SUP_GENE"
                continue
        mention_lemmas = set([x.lemma.casefold() for x in mention.words])
        name_words = set([x.casefold() for x in
                          mention.entity.split("|")[1].split()])
        # The mention is exactly the HPO name
        if mention_lemmas == name_words and \
                mention.words[0].lemma != "pneunomiae":
            mention.is_correct = True
            mention.type = "HPOTERM_SUP_FULL"
    return mentions


# Add features
def add_features(mention, sentence):
    # The first alphanumeric lemma on the left of the mention, if present,
    idx = mention.wordidxs[0] - 1
    left_lemma_idx = -1
    while idx >= 0 and not sentence.words[idx].word.isalnum():
        idx -= 1
    try:
        mention.left_lemma = sentence.words[idx].lemma
        try:
            float(mention.left_lemma)
            mention.left_lemma = "_NUMBER"
        except ValueError:
            pass
        left_lemma_idx = idx
        mention.add_feature("NGRAM_LEFT_1_[{}]".format(
            mention.left_lemma))
    except IndexError:
        pass
    # The first alphanumeric lemma on the right of the mention, if present,
    idx = mention.wordidxs[-1] + 1
    right_lemma_idx = -1
    while idx < len(sentence.words) and not sentence.words[idx].word.isalnum():
        idx += 1
    try:
        mention.right_lemma = sentence.words[idx].lemma
        try:
            float(mention.right_lemma)
            mention.right_lemma = "_NUMBER"
        except ValueError:
            pass
        right_lemma_idx = idx
        mention.add_feature("NGRAM_RIGHT_1_[{}]".format(
            mention.right_lemma))
    except IndexError:
        pass
    # The lemma "two on the left" of the mention, if present
    try:
        mention.add_feature("NGRAM_LEFT_2_[{}]".format(
            sentence.words[left_lemma_idx - 1].lemma))
        mention.add_feature("NGRAM_LEFT_2_C_[{} {}]".format(
            sentence.words[left_lemma_idx - 1].lemma,
            mention.left_lemma))
    except IndexError:
        pass
    # The lemma "two on the right" on the left of the mention, if present
    try:
        mention.add_feature("NGRAM_RIGHT_2_[{}]".format(
            sentence.words[right_lemma_idx + 1].lemma))
        mention.add_feature("NGRAM_RIGHT_2_C_[{} {}]".format(
            mention.right_lemma,
            sentence.words[right_lemma_idx + 1].lemma))
    except IndexError:
        pass
    # The keywords that appear in the sentence with the mention
    minl = 100
    minp = None
    minw = None
    for word in mention.words:
        for word2 in sentence.words:
            if word2.lemma in KEYWORDS:
                p = sentence.get_word_dep_path(word.in_sent_idx,
                                               word2.in_sent_idx)
                kw = word2.lemma
                if word2.lemma in PATIENT_KWS:
                    kw = "_HUMAN"
                mention.add_feature("KEYWORD_[" + kw + "]" + p)
                if len(p) < minl:
                    minl = len(p)
                    minp = p
                    minw = kw
    # Special feature for the keyword on the shortest dependency path
    if minw:
        mention.add_feature('EXT_KEYWORD_MIN_[' + minw + ']' + minp)
        mention.add_feature('KEYWORD_MIN_[' + minw + ']')
    # The verb closest to the candidate
    minl = 100
    minp = None
    minw = None
    for word in mention.words:
        for word2 in sentence.words:
            if word2.word.isalpha() and re.search('^VB[A-Z]*$', word2.pos) \
                    and word2.lemma != 'be':
                p = sentence.get_word_dep_path(word.in_sent_idx,
                                               word2.in_sent_idx)
                if len(p) < minl:
                    minl = len(p)
                    minp = p
                    minw = word2.lemma
        if minw:
            mention.add_feature('VERB_[' + minw + ']' + minp)


# Return a list of mention candidates extracted from the sentence
def extract(sentence):
    mentions = []
    mention_ids = set()
    # If there are no English words in the sentence, we skip it.
    no_english_words = True
    for word in sentence.words:
        word.stem = stemmer.stem(word.word)  # Here so all words have stem
        if len(word.word) > 2 and \
                (word.word in english_dict or
                 word.word.casefold() in english_dict):
            no_english_words = False
    if no_english_words:
        return mentions
    history = set()
    # Iterate over each phrase of length at most max_mention_length
    for start, end in get_all_phrases_in_sentence(sentence,
                                                  max_mention_length):
        if start in history or end - 1 in history:
            continue
        phrase = " ".join([word.word for word in sentence.words[start:end]])
        # If the phrase is a gene long name containing a phenotype name, create
        # a candidate that we supervise as negative
        if len(phrase) > 1 and phrase in genes_with_hpoterm:
            mention = Mention("HPOTERM_SUP_GENEL",
                              phrase,
                              sentence.words[start:end])
            mention.is_correct = False
            add_features(mention, sentence)
            mentions.append(mention)
            for word in sentence.words[start:end]:
                history.add(word.in_sent_idx)
            continue
    # Iterate over each phrase of length at most max_mention_length
    for start, end in get_all_phrases_in_sentence(sentence,
                                                  max_mention_length):
        should_continue = False
        for i in range(start, end):
            if i in history:
                should_continue = True
                break
        if should_continue:
            continue
        phrase = " ".join([word.word for word in sentence.words[start:end]])
        # The list of stems in the phrase (not from stopwords or symbols, and
        # not already used for a mention)
        phrase_stems = []
        for word in sentence.words[start:end]:
            if not re.match("^(_|\W)+$", word.word) and \
                    (len(word.word) == 1 or
                     word.lemma.casefold() not in stopwords_dict):
                phrase_stems.append(word.stem)
        phrase_stems_set = frozenset(phrase_stems)
        if phrase_stems_set in hpoterms_dict:
            # Find the word objects of that match
            mention_words = []
            mention_lemmas = []
            mention_stems = []
            for word in sentence.words[start:end]:
                if word.stem in phrase_stems_set and \
                        word.lemma.casefold() not in mention_lemmas and \
                        word.stem not in mention_stems:
                    mention_lemmas.append(word.lemma.casefold())
                    mention_words.append(word)
                    mention_stems.append(word.stem)
                    if len(mention_words) == len(phrase_stems_set):
                        break
            entity = list(hpoterms_dict[phrase_stems_set])[0]
            mention = Mention(
                "HPOTERM", hponames_to_ids[entity] + "|" + entity,
                mention_words)
            # The following is a way to avoid duplicates.
            # It's ugly and not perfect
            if mention.id() in mention_ids:
                continue
            mention_ids.add(mention.id())
            # Features
            add_features(mention, sentence)
            mentions.append(mention)
            for word in mention_words:
                history.add(word.in_sent_idx)
    # Generate some negative candidates at random, if this sentences didn't
    # contain any other candidate. We want the candidates to be nouns.
    if len(mentions) == 0 and random.random() <= NEG_PROB:
        index = random.randint(0, len(sentence.words) - 1)
        # We may not get a noun at random, so we try again if we don't.
        tries = 10
        while not sentence.words[index].pos.startswith("NN") and tries > 0:
            index = random.randint(0, len(sentence.words) - 1)
            tries -= 1
        if sentence.words[index].pos.startswith("NN"):
            mention = Mention(
                "HPOTERM_SUP_rand", sentence.words[index].lemma.casefold(),
                sentence.words[index:index+1])
            mention.is_correct = False
            add_features(mention, sentence)
            mentions.append(mention)
    return mentions


if __name__ == "__main__":
    # Process the input
    with fileinput.input() as input_files:
        for line in input_files:
            # Parse the TSV line
            line_dict = get_dict_from_TSVline(
                line,
                ["doc_id", "sent_id", "wordidxs", "words", "poses", "ners",
                    "lemmas", "dep_paths", "dep_parents", "bounding_boxes"],
                [no_op, int, lambda x: TSVstring2list(x, int), TSVstring2list,
                    TSVstring2list, TSVstring2list, TSVstring2list,
                    TSVstring2list, lambda x: TSVstring2list(x, int),
                    TSVstring2list])
            # Create the Sentence object
            sentence = Sentence(
                line_dict["doc_id"], line_dict["sent_id"],
                line_dict["wordidxs"], line_dict["words"], line_dict["poses"],
                line_dict["ners"], line_dict["lemmas"], line_dict["dep_paths"],
                line_dict["dep_parents"], line_dict["bounding_boxes"])
            # Skip weird sentences
            if sentence.is_weird():
                continue
            # Extract mention candidates
            mentions = extract(sentence)
            # Supervise
            new_mentions = supervise(mentions, sentence)
            # Print!
            for mention in new_mentions:
                print(mention.tsv_dump())
