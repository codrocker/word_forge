-- scripts/replicate/mysql_schema.sql
-- Source of truth: feishu wiki https://lpt2q1lbzh.feishu.cn/wiki/wikcnQFiS6CvAj8sfXW86mK1d2G
-- Apply once, manually:
--   mysql -h 120.27.242.42 -u wordforge_writer -p word_forge < scripts/replicate/mysql_schema.sql

-- 4.1 word
CREATE TABLE `word` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `word_id` bigint NOT NULL COMMENT '单词ID',
  `type` bigint NOT NULL COMMENT '单词类型,1-单词。2-短语',
  `form` varchar(255) NOT NULL COMMENT '单词的形式',
  `phonetic_us` varchar(255) NOT NULL COMMENT '美式kk音标',
  `audio_us` varchar(255) DEFAULT NULL,
  `phonetic_uk` varchar(255) NOT NULL,
  `audio_uk` varchar(255) DEFAULT NULL,
  `meanings` TEXT DEFAULT NULL COMMENT '[{"id":meaning_id},...]',
  `mnemonics` TEXT DEFAULT NULL COMMENT '[{"id":mnemonic_id},...]',
  `plural` varchar(255) DEFAULT NULL,
  `phrases` TEXT DEFAULT NULL,
  `structure` TEXT DEFAULT NULL,
  `third_person` varchar(255) DEFAULT NULL,
  `present_participle` varchar(255) DEFAULT NULL,
  `past_tense` varchar(255) DEFAULT NULL,
  `past_participle` varchar(255) DEFAULT NULL,
  `base` bigint DEFAULT NULL,
  `comparative` varchar(255) DEFAULT NULL,
  `superlative` varchar(255) DEFAULT NULL,
  `derivatives` TEXT DEFAULT NULL,
  `morpheme_derivatives` TEXT DEFAULT NULL,
  `family` TEXT DEFAULT NULL,
  `source` TEXT DEFAULT NULL,
  `status` bigint NOT NULL COMMENT '0=待审核,1=已上线,2=已删除',
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `word_id` (`word_id`),
  KEY `form` (`form`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4.2 meaning
CREATE TABLE `meaning` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `meaning_id` bigint NOT NULL,
  `word_id` bigint NOT NULL,
  `user_group` bigint DEFAULT NULL COMMENT '用户群分组(wiki 改名,非 group)',
  `pos` bigint DEFAULT NULL COMMENT '词性枚举: 1-n, 2-v, 3-adj, 4-adv, 5-prep, 6-conj, 7-pron, 8-interj, 9-num, 10-art, 201-phrasal_verb (与 wordforge _POS_MAP 对齐)',
  `pos_sub` bigint DEFAULT NULL,
  `equivalents` TEXT DEFAULT NULL COMMENT '["直译词1","直译词2"] — 纯字符串数组',
  `synonyms` TEXT DEFAULT NULL COMMENT '[{"id":word_id}]',
  `antonyms` TEXT DEFAULT NULL COMMENT '[{"id":word_id}]',
  `phonetic_us` varchar(255) DEFAULT NULL,
  `audio_us` varchar(255) DEFAULT NULL,
  `phonetic_uk` varchar(255) DEFAULT NULL,
  `audio_uk` varchar(255) DEFAULT NULL,
  `cn_paraphrase` TEXT DEFAULT NULL,
  `en_paraphrase` TEXT DEFAULT NULL,
  `sentences` TEXT DEFAULT NULL COMMENT '[{"sentence_id":123}]',
  `source` TEXT DEFAULT NULL,
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `meaning_id` (`meaning_id`),
  KEY `word_id` (`word_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4.3 sentence
CREATE TABLE `sentence` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `word_id` bigint NOT NULL,
  `meaning_id` bigint NOT NULL,
  `sentence_id` bigint NOT NULL,
  `user_group` bigint DEFAULT NULL,
  `form` TEXT,
  `highlight` varchar(255) DEFAULT NULL COMMENT '[[start,end],...] 整数区间对',
  `translation` TEXT NOT NULL,
  `audio_us` varchar(255),
  `audio_uk` varchar(255),
  `source` TEXT DEFAULT NULL,
  `citation` bigint DEFAULT NULL COMMENT '句子出处 id(wiki 新列,momo 实例的 source 改名)',
  `citation_detail` TEXT COMMENT '出处详情(wiki 改名,实例里是 source_detail)',
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `sentence_id` (`sentence_id`),
  KEY `meaning_id` (`meaning_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4.4 mnemonic
CREATE TABLE `mnemonic` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `word_id` bigint NOT NULL,
  `mnemonic_id` bigint NOT NULL,
  `type` bigint NOT NULL COMMENT '1-谐音联想',
  `user_group` bigint NOT NULL,
  `content` TEXT NOT NULL COMMENT 'wordforge 产出格式: {"kind":"phonetic","text":"..."}',
  `source` TEXT DEFAULT NULL,
  `creator_id` bigint NOT NULL COMMENT 'LLM 产出填 0; 模型名写 source',
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `mnemonic_id` (`mnemonic_id`),
  KEY `word_id` (`word_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4.5 phrase
CREATE TABLE `phrase` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `phrase_id` bigint NOT NULL,
  `form` varchar(255) NOT NULL,
  `meaning` TEXT NOT NULL,
  `audio_us` varchar(255) NOT NULL,
  `audio_uk` varchar(255) NOT NULL,
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `phrase_id` (`phrase_id`),
  KEY `form` (`form`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4.6 Shadow 副本
CREATE TABLE word_shadow LIKE word;
CREATE TABLE meaning_shadow LIKE meaning;
CREATE TABLE sentence_shadow LIKE sentence;
CREATE TABLE mnemonic_shadow LIKE mnemonic;
CREATE TABLE phrase_shadow LIKE phrase;
