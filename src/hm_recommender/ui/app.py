"""Local UI; receipts keep their UUID and payload until acknowledged."""
from datetime import date, datetime, time, timedelta, timezone
import os
from uuid import uuid4
import httpx
import streamlit as st


def main():
    st.set_page_config(page_title='H&M · Recomendaciones',page_icon='🧥',layout='wide')
    st.title('Tu próxima selección')
    st.caption('Explora el catálogo y comprueba cómo tus compras actualizan las recomendaciones.')
    base=os.environ.get('HMREC_API_URL','http://api:8000')
    client=httpx.Client(base_url=base,trust_env=False,timeout=120,follow_redirects=False)
    def call(method,path,**kwargs):
        response=client.request(method,path,**kwargs)
        response.raise_for_status()
        return response.json()
    try:
        if 'pending' not in st.session_state:st.session_state['pending']=None
        def submit(payload):
            st.session_state['pending']=payload
            try:
                result=call('POST','/events',json=payload)
                st.session_state['pending']=None
                st.session_state['pending_terminal']=False
                st.session_state['receipt']=result
                st.session_state['notice']=('success','Evento guardado. Actualiza las recomendaciones para incorporar las compras.')
            except httpx.HTTPError as error:
                terminal=(isinstance(error,httpx.HTTPStatusError) and 400<=error.response.status_code<500
                          and error.response.status_code not in (408,429))
                st.session_state['pending_terminal']=terminal
                message=('La versión o el contenido del evento no es compatible. Cierra este intento para continuar; cualquier evento ya guardado se conserva.'
                         if terminal else 'No se confirmó el evento. Reintenta la misma operación; no se duplicará la compra.')
                st.session_state['notice']=('error',message)
            st.rerun()
        if st.session_state.get('notice'):
            level,message=st.session_state.pop('notice');getattr(st,level)(message)
        if st.session_state['pending']:
            st.warning('Hay un evento pendiente de confirmación.')
            terminal=st.session_state.get('pending_terminal',False)
            if st.button('Reintentar evento pendiente',key='retry',disabled=terminal):submit(st.session_state['pending'])
            if terminal:
                st.caption('Cerrar el intento permite otra operación y no revierte compras guardadas.')
                if st.button('Cerrar este intento',key='discard_pending'):
                    st.session_state['pending']=None;st.session_state['pending_terminal']=False;st.rerun()
        health=call('GET','/health/ready')
        streams=call('GET','/streams')['items']
        if not streams:
            st.info('El servicio está listo. Prepara un entorno de demostración para comenzar.');return
        chosen=st.selectbox('Entorno',streams,format_func=lambda s:f"{s['mode']} · {s['stream_id'][:8]}",key='stream')
        stream=chosen['stream_id'];release=chosen['release_id']
        cutoff=date.fromisoformat(chosen['data_as_of'])
        st.caption(f"Versión {release} · Corte {cutoff} · Revisión {chosen['revision']}")
        st.caption(f"Última actualización {chosen['published_at']} · Datos recibidos hasta {chosen['received_before']}")
        st.info('Entorno de simulación. Las compras de esta demo se conservan separadas del historial original.')
        customers=call('GET','/customers')['items']
        def select_customer():st.session_state['customer']=st.session_state['selected_customer']
        selected=st.selectbox('Cliente existente',customers or ['demo-visitor'],key='selected_customer',on_change=select_customer)
        customer=st.text_input('Cliente para esta sesión',value=selected,key='customer').strip()
        if not customer:st.warning('Escribe o selecciona un cliente.');return
        page=st.number_input('Página del catálogo',min_value=1,value=1,step=1)
        catalog=call('GET','/catalog',params={'offset':(page-1)*12,'limit':12})
        filters={}
        columns=st.columns(3)
        for column,(field,label) in zip(columns,[('product_type_name','Tipo'),('product_group_name','Grupo'),('colour_group_name','Color')]):
            value=column.selectbox(label,['Todos',*catalog['filters'][field]])
            if value!='Todos':filters[field]=value
        result=call('POST',f'/streams/{stream}/recommend',json={'customer_id':customer,'k':12,'filters':filters})
        st.subheader('Recomendado para ti')
        st.caption('Popularidad de siete días' if result['fallback'] else 'Compras recientes y popularidad de siete días')
        columns=st.columns(4)
        for i,item in enumerate(result['items']):
            with columns[i%4]:
                response=client.get(f"/articles/{item['article_id']}/image")
                if response.status_code==200:
                    st.image(response.text if response.headers.get('content-type','').startswith('image/svg') else response.content,width='stretch')
                st.write(f"{item['rank']}. Artículo {item['article_id']}")
                st.caption('Compra reciente' if item['reason']=='recent_purchase' else 'Popular esta semana')
        if not result['items']:st.info('No hay artículos disponibles con estos filtros.')
        st.subheader(f"Catálogo · {catalog['total']:,} artículos")
        if catalog['items']:
            st.dataframe(catalog['items'],hide_index=True,width='stretch')
            article=st.selectbox('Artículo',catalog['items'],format_func=lambda a:f"{a['article_id']} · {a.get('product_type_name') or 'Sin tipo'}")
            event_day=st.date_input('Fecha de la interacción',value=cutoff-timedelta(days=1))
            def payload(kind):
                return dict(event_id=str(uuid4()),stream_id=stream,release_id=release,customer_id=customer,
                    article_id=article['article_id'],kind=kind,origin=chosen['mode'],
                    event_time=datetime.combine(event_day,time(12),tzinfo=timezone.utc).isoformat())
            buttons=st.columns(3)
            for column,kind,label in zip(buttons,('purchase','view','click'),('Registrar compra','Registrar vista','Registrar clic')):
                if column.button(label,key=kind,disabled=bool(st.session_state['pending'])):submit(payload(kind))
        st.subheader('Actualizar selección')
        new_cutoff=st.date_input('Compras anteriores a',value=cutoff,min_value=cutoff)
        st.caption('La fecha es un reloj de simulación. Vistas y clics quedan registrados, pero sólo las compras cambian el historial.')
        if st.button('Actualizar recomendaciones',key='refresh'):
            call('POST',f'/admin/streams/{stream}/refresh',json={'data_as_of':str(new_cutoff)},
                headers={'Authorization':'Bearer '+os.environ.get('HM_APP_PASSWORD','')})
            st.rerun()
    except httpx.HTTPError:
        st.error('El servicio no está disponible o la versión cambió. Recarga la página; los eventos confirmados se conservan.')
    finally:client.close()


if __name__=='__main__':main()
