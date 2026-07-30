from aiogram import types
from aiogram.filters import Text
from config import router
from states import user_states
from .wallet import handle_import_seed
from .withdraw import handle_enter_address

@router.message(Text())
async def handle_text(message: types.Message):
    user_id = str(message.from_user.id)
    if user_id not in user_states:
        return
    step = user_states[user_id].get("step")
    if step == "import_seed":
        await handle_import_seed(message)
    elif step == "enter_address":
        await handle_enter_address(message)
    # else - ignore
