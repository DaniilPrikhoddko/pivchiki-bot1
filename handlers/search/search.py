import os
from os import remove
from aiogram import F, types, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sources.search.search import find_most_similar_song
# импортируем статусы
from states.states_search import SearchStates
# парсер из ютуба
from sources.parsers.YouTubeBomber import find_in_youtube, download_song
# методы бд
from sources.postgres.sql_requests import save_search_history


router = Router()


@router.message(F.text.endswith("Поиск музыки"))
async def start_search(message: types.Message, state: FSMContext):


    markup = InlineKeyboardBuilder()
    by_text = types.InlineKeyboardButton(
        text="✍️ По текстовому вводу",
        callback_data="text"
    )
    by_audio = types.InlineKeyboardButton(
        text="🎤 По голосовому сообщению",
        callback_data="audio"
    )
    markup.add(by_text, by_audio)
    markup.adjust(1, 1)

    sent_message = await message.answer(text="Выберите тип поиска:", reply_markup=markup.as_markup())
    await state.update_data(last_message_id=sent_message.message_id)
    await state.set_state(SearchStates.choose_method)


# случай, если текст
@router.callback_query(F.data == "text", SearchStates.choose_method)
async def get_info_about_song(callback: types.CallbackQuery, state: FSMContext):
    mes_text = "Принято! Тогда введите свой запрос. Укажите название песни и/или автора:"
    await callback.message.edit_text(text=mes_text)
    await state.set_state(SearchStates.wait_info_about_song)


@router.message(F.text, SearchStates.wait_info_about_song)
async def request_processing(message: types.Message, state: FSMContext):
    # сохранить в бд запрос и время запроса
    user_id = message.from_user.id
    save_search_history(user_id, message.text)

    sent_message = await message.answer("Ищу! Минутку...")
    await state.update_data(search_message_id=sent_message.message_id)

    answer = find_in_youtube(message.text)
    if not answer[0]:
        await message.answer(answer[1])
        return

    result = answer[1]
    data = answer[2]
    count = answer[3]

    # сохраняем данные поиска
    await state.update_data({"result": data})

    markup = InlineKeyboardBuilder()
    for i in range(count):
        btn = types.InlineKeyboardButton(
            text=f"{i + 1}",
            callback_data=f"song_{i + 1}"
        )
        markup.add(btn)

    markup.adjust(5, 5)

    data = await state.get_data()
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=data["search_message_id"],
        text=result,
        reply_markup=markup.as_markup()
    )
    await state.set_state(SearchStates.send_song)


@router.callback_query(
    F.data.in_([f"song_{i + 1}" for i in range(10)]),
    SearchStates.send_song
)
async def send_song(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Замечательный выбор! Загружаю...")
    # получаем результаты хендлера выше
    data = await state.get_data()
    result = data["result"]

    path = "sources/songs"

    # TODO сделать редирект на добавление
    markup = InlineKeyboardBuilder()
    markup.add(types.InlineKeyboardButton(
        text="➕ Добавить в плейлист",
        callback_data="add_song"
    ))

    success, track_data = download_song(result, int(callback.data[-1]), path)

    if not success:
        await callback.message.answer("Не удалось скачать, попробуйте позже еще раз")
        return

    # Формируем путь к файлу (как это делается в download_song)
    filename = os.path.join(path, f"{track_data['title']}.mp3")
    mp3_path = f"{filename}.mp3"

    file = FSInputFile(mp3_path)
    await callback.message.answer_audio(file, reply_markup=markup.as_markup())
    await state.clear()


# случай, если гс
@router.callback_query(F.data == "audio", SearchStates.choose_method)
async def get_voice(callback: types.CallbackQuery, state: FSMContext):
    mes_text = "Хорошо! Тогда запишите голосовое сообщение, где будет слышно песню, примерно на 30 сек."
    await callback.message.edit_text(text=mes_text)
    await state.set_state(SearchStates.wait_audio)


@router.message(F.voice, SearchStates.wait_audio)
async def voice_processing(message: types.Message, state: FSMContext, bot: Bot):
    await message.answer("Услышал вас. Уже ищу!")
    file_path = f"{message.voice.file_id}.ogg"
    await bot.download(message.voice.file_id, destination=file_path)

    nearest_song, max_similarity = find_most_similar_song(file_path)
    path = f"sources/songs/{nearest_song}"

    # сохранить в бд запрос и время запроса
    user_id = message.from_user.id
    save_search_history(user_id, nearest_song)
    # TODO сделать редирект на добавление
    markup = InlineKeyboardBuilder()
    markup.add(types.InlineKeyboardButton(
        text="➕ Добавить в плейлист",
        callback_data="add_song"
    ))

    if max_similarity > 0.5:
        file = FSInputFile(path)
        mes_text = f"Я нашел:\n{nearest_song}"
        await message.answer_audio(file, caption=mes_text, reply_markup=markup.as_markup())
        remove(file_path)
        await state.clear()
    else:
        text = "К сожалению я не смог найти песню. Попробуйте повторить поиск"
        await message.answer(text=text)
        await state.clear()


@router.message(SearchStates.wait_audio)
async def voice_processing(message: types.Message, state: FSMContext):
    mes_text = "К сожалению это не голосовое сообщение\nПопробуйте записать его снова"
    await message.answer(text=mes_text)
    await state.set_state(SearchStates.wait_audio)